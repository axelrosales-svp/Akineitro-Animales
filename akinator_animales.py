# akinator_animales.py
# ---------------------
# Versión con atenuación de pico anti-saturación mecánica (45% volumen máximo)
# para que las pequeñas bocinas de 1W/2W del kit suenen suaves, nítidas y sin carraspeos.

import struct
import time
import os
import sys
import gc
import micropython
import network
from machine import I2S, I2C, Pin, ADC, SoftI2C
import ssd1306

gc.collect()

# ==========================================
# 0. APAGAR WIFI PARA PREVENIR RUIDO RF
# ==========================================
try:
    wlan_ap = network.WLAN(network.AP_IF)
    wlan_ap.active(False)
    wlan_sta = network.WLAN(network.STA_IF)
    wlan_sta.active(False)
except:
    pass

# ==========================================
# 1. CONFIGURACIÓN DE PINES Y HARDWARE
# ==========================================

# Pantalla OLED con SoftI2C a 100kHz
i2c = None
oled = None
try:
    i2c = SoftI2C(scl=Pin(9), sda=Pin(8), freq=100000)
    oled = ssd1306.SSD1306_I2C(128, 64, i2c)
except:
    pass

# Micrófono INMP441 (I2S 1 RX, 32-bits Estéreo, ibuf=4096)
audio_in = I2S(
    1, sck=Pin(15), ws=Pin(7), sd=Pin(16),
    mode=I2S.RX, bits=32, format=I2S.STEREO, rate=16000, ibuf=4096
)

# Bocina MAX98357A (I2S 0 TX, 16-bits STEREO para reloj BCLK 32x exacto)
audio_out = I2S(
    0, sck=Pin(5), ws=Pin(4), sd=Pin(6),
    mode=I2S.TX, bits=16, format=I2S.STEREO, rate=16000, ibuf=8192
)

# Potenciómetro para control de volumen (GPIO 2)
pot = ADC(Pin(2))
pot.atten(ADC.ATTN_11DB)

# ==========================================
# 2. CONFIGURACIÓN Y AJUSTES DE VOZ
# ==========================================
MIC_CHANNEL = 0             # 0 = Izquierdo (GND), 1 = Derecho (VCC)
MIC_SHIFT = 12              # GANANCIA DE MICRÓFONO (16x boost)
TIMEOUT_SEGUNDOS = 5        # Ventana máxima de 5 segundos para responder
TIEMPO_SILENCIO_FIN = 300   # Silencio post-habla (ms) para cortar grabación

DURACION_MINIMA_VOZ = 160   # Ruidos < 160ms se descartan
LIMITE_DURACION_SI_NO = 320 # < 320ms es "NO" (corto), >= 320ms es "SÍ" (largo SÍII)

# Búferes estáticos pre-asignados
MONO_READ_BUF = bytearray(1024)
STEREO_PLAY_BUF = bytearray(2048)
MIC_FLUSH_BUF = bytearray(1600)
DSP_MIC_BUF = bytearray(800)

# ==========================================
# 3. INTERFAZ GRÁFICA DE CONSOLA Y OLED
# ==========================================

def enviar_telemetria_gui(evento, detalle=""):
    """Envía comandos estructurados por el puerto serie para actualizar la GUI de la PC."""
    sys.stdout.write(f"[GUI_EVENT]:{evento}:{detalle}\n")

def mostrar(titulo, linea1="", linea2=""):
    """Dibuja en la pantalla OLED y transmite el evento a la GUI."""
    if oled:
        try:
            oled.fill(0)
            oled.text(titulo, 0, 0)
            oled.hline(0, 10, 128, 1)
            oled.text(linea1[:16], 0, 20)
            oled.text(linea2[:16], 0, 40)
            oled.show()
        except:
            pass

    enviar_telemetria_gui("OLED", f"{titulo}|{linea1}|{linea2}")

def mostrar_texto_largo(titulo, texto):
    """Empaca texto largo para la OLED e imprime en la consola."""
    if oled:
        try:
            oled.fill(0)
            oled.text(titulo, 0, 0)
            oled.hline(0, 10, 128, 1)

            palabras = texto.split()
            linea_actual = ""
            y_pos = 15

            for palabra in palabras:
                if len(linea_actual) == 0:
                    linea_actual = palabra
                elif len(linea_actual + " " + palabra) <= 16:
                    linea_actual += " " + palabra
                else:
                    oled.text(linea_actual, 0, y_pos)
                    y_pos += 10
                    linea_actual = palabra

                    if y_pos >= 60:
                        oled.show()
                        time.sleep(2.0)
                        oled.fill(0)
                        oled.text(titulo, 0, 0)
                        oled.hline(0, 10, 128, 1)
                        y_pos = 15

            if linea_actual:
                oled.text(linea_actual, 0, y_pos)

            oled.show()
        except:
            pass
            
    enviar_telemetria_gui("PREGUNTA", texto)

# ==========================================
# 4. DSP ACELERADO POR HARDWARE (VIPER)
# ==========================================

@micropython.viper
def expand_mono16_to_stereo16_and_scale(src_mono: ptr16, dest_stereo: ptr16, num_samples: int, vol_fp: int):
    """Aplica escala de volumen suave (máximo 45%) para evitar distorsión mecánica en la bocina."""
    i = 0
    while i < num_samples:
        s16 = src_mono[i]
        val = (s16 * vol_fp) >> 8
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
        
        idx_st = i << 1
        dest_stereo[idx_st] = val
        dest_stereo[idx_st + 1] = val
        i += 1

@micropython.viper
def get_max_amplitude_stereo(src_32: ptr32, length: int, channel: int) -> int:
    """Extrae el canal seleccionado y calcula la amplitud máxima en C nativo."""
    i = 0
    max_val = 0
    shift_val = int(MIC_SHIFT)
    while i < length:
        s32 = src_32[(i << 1) + channel]
        s_amp = s32 >> shift_val
        if s_amp < 0:
            s_amp = -s_amp
        if s_amp > max_val:
            max_val = s_amp
        i += 1
    return max_val

# ==========================================
# 5. FUNCIONES DE REPRODUCCIÓN Y DETECCIÓN
# ==========================================

def leer_volumen_potenciometro_fp():
    """Mapea el potenciómetro de 10% (25) a 45% (115) para evitar saturación de la bocina."""
    try:
        valor_crudo = pot.read()
        vol_fp = 25 + (valor_crudo * 90) // 4095
    except:
        vol_fp = 95 # 37% de volumen seguro y nítido
    return vol_fp

def reproducir_wav(archivo):
    """Reproduce un archivo WAV de /audios_akinator/ con volumen controlado anti-saturación."""
    ruta_completa = "/audios_akinator/" + archivo
    enviar_telemetria_gui("AUDIO", archivo)
    gc.collect()
    try:
        vol_fp = leer_volumen_potenciometro_fp()
        
        with open(ruta_completa, "rb") as f:
            f.seek(44)  # Salta la cabecera WAV
            while True:
                bytes_read = f.readinto(MONO_READ_BUF)
                if bytes_read == 0:
                    break
                
                num_muestras = bytes_read // 2
                expand_mono16_to_stereo16_and_scale(MONO_READ_BUF, STEREO_PLAY_BUF, num_muestras, vol_fp)
                
                bytes_to_write = num_muestras * 4
                audio_out.write(STEREO_PLAY_BUF[:bytes_to_write])
                    
        time.sleep_ms(800)

    except OSError:
        enviar_telemetria_gui("ERROR", f"No se encontro {ruta_completa}")
        time.sleep(1)

def vaciar_buffer_mic():
    """Descarta muestras viejas del micrófono I2S."""
    try:
        for _ in range(40):
            audio_in.readinto(MIC_FLUSH_BUF)
    except:
        pass

def calibrar_ruido():
    """Mide el ruido ambiente y calcula un umbral de seguridad mínimo de 2500."""
    mostrar("CALIBRANDO...", "Guarda silencio", "midiendo salon")
    reproducir_wav("calibrando.wav")
    vaciar_buffer_mic()

    lecturas = []
    
    for _ in range(30):
        num_bytes = audio_in.readinto(DSP_MIC_BUF)
        if num_bytes > 0:
            max_amp = get_max_amplitude_stereo(DSP_MIC_BUF, num_bytes // 8, MIC_CHANNEL)
            lecturas.append(max_amp)
        time.sleep(0.1)

    ruido_base = sum(lecturas) // len(lecturas) if lecturas else 400
    umbral = max(ruido_base * 3, 2500)

    enviar_telemetria_gui("CALIBRACION", f"Base={ruido_base},Umbral={umbral}")
    mostrar("LISTO", f"Base: {ruido_base}", f"Umbral: {umbral}")
    time.sleep(1.5)
    return umbral

def escuchar_si_no(umbral):
    """Escucha la respuesta durante 5 segundos con inmunidad a siseos de fondo."""
    vaciar_buffer_mic()
    enviar_telemetria_gui("ESTADO", "ESCUCHANDO")

    inicio_escucha_ms = time.ticks_ms()
    inicio_voz = 0
    fin_voz = 0
    hablando = False
    umbral_silencio = umbral * 0.6
    ultimo_segundo_mostrado = -1

    while True:
        transcurrido_ms = time.ticks_diff(time.ticks_ms(), inicio_escucha_ms)
        segundos_restantes = 5 - (transcurrido_ms // 1000)

        if segundos_restantes <= 0 and not hablando:
            break

        if not hablando and segundos_restantes != ultimo_segundo_mostrado:
            ultimo_segundo_mostrado = segundos_restantes
            mostrar("RESPONDE AHORA", f"Tiempo: {segundos_restantes}s", "SI (largo)/NO(corto)")
            enviar_telemetria_gui("RELOJ", str(segundos_restantes))

        num_bytes = audio_in.readinto(DSP_MIC_BUF)
        if num_bytes > 0:
            if transcurrido_ms < 300:
                continue

            max_amp = get_max_amplitude_stereo(DSP_MIC_BUF, num_bytes // 8, MIC_CHANNEL)

            if max_amp > umbral and not hablando:
                hablando = True
                inicio_voz = time.ticks_ms()
                fin_voz = inicio_voz
                enviar_telemetria_gui("ESTADO", "GRABANDO")
                mostrar("GRABANDO...", "Midiendo", "duracion voz...")

            elif hablando:
                if max_amp > umbral_silencio:
                    fin_voz = time.ticks_ms()

                if time.ticks_diff(time.ticks_ms(), fin_voz) > TIEMPO_SILENCIO_FIN:
                    duracion = time.ticks_diff(fin_voz, inicio_voz)

                    if duracion < DURACION_MINIMA_VOZ:
                        hablando = False
                        enviar_telemetria_gui("ESTADO", "ESCUCHANDO")
                        mostrar("RESPONDE AHORA", f"Tiempo: {segundos_restantes}s", "Intenta de nuevo")
                        continue

                    if duracion < LIMITE_DURACION_SI_NO:
                        sys.stdout.write(f"🎙️ [Voz]: {duracion}ms -> Clasificado como 'NO'\n")
                        enviar_telemetria_gui("RESPUESTA", "NO")
                        return "no"
                    else:
                        sys.stdout.write(f"🎙️ [Voz]: {duracion}ms -> Clasificado como 'SÍ'\n")
                        enviar_telemetria_gui("RESPUESTA", "SI")
                        return "si"

        time.sleep_ms(2)

    enviar_telemetria_gui("RESPUESTA", "TIMEOUT")
    return None

# ==========================================
# 6. ARBOL DE DECISION (7 animales)
# ==========================================

ARBOL_ANIMALES = {
    "pregunta": "es_domestico",
    "si": {
        "pregunta": "ladra_mejor_amigo",
        "si": {"animal": "Perro"},
        "no": {
            "pregunta": "hocico_plano_cola_rizada",
            "si": {"animal": "Cerdo"},
            "no": {"animal": "Burro"},
        },
    },
    "no": {
        "pregunta": "orejas_largas_saltos",
        "si": {"animal": "Conejo"},
        "no": {
            "pregunta": "melena_sabana",
            "si": {"animal": "Leon"},
            "no": {
                "pregunta": "bambu_blanco_negro",
                "si": {"animal": "Panda"},
                "no": {"animal": "Oso"},
            },
        },
    },
}

TEXTOS_PREGUNTA = {
    "es_domestico": "Es un animal domestico o de granja?",
    "ladra_mejor_amigo": "Es mascota que ladra y es el mejor amigo del hombre?",
    "hocico_plano_cola_rizada": "Tiene hocico plano y cola rizada?",
    "orejas_largas_saltos": "Tiene orejas largas y se mueve a saltos?",
    "melena_sabana": "Es felino de sabana y el macho tiene melena?",
    "bambu_blanco_negro": "Es blanco y negro y come bambu?",
}

AUDIO_PREGUNTA = {
    "es_domestico": "p_domestico.wav",
    "ladra_mejor_amigo": "p_ladra.wav",
    "hocico_plano_cola_rizada": "p_hocico.wav",
    "orejas_largas_saltos": "p_orejas.wav",
    "melena_sabana": "p_melena.wav",
    "bambu_blanco_negro": "p_bambu.wav",
}

AUDIO_RESULTADO = {
    "Perro": "r_perro.wav",
    "Cerdo": "r_cerdo.wav",
    "Burro": "r_burro.wav",
    "Conejo": "r_conejo.wav",
    "Leon": "r_leon.wav",
    "Panda": "r_panda.wav",
    "Oso": "r_oso.wav",
}

# ==========================================
# 7. FLUJO PRINCIPAL DEL JUEGO
# ==========================================

try:
    enviar_telemetria_gui("SISTEMA", "INICIADO")

    mostrar("AKINATOR", "de animales", "Iniciando...")
    reproducir_wav("bienvenida.wav")

    UMBRAL = calibrar_ruido()

    nodo_actual = ARBOL_ANIMALES

    while "animal" not in nodo_actual:
        clave = nodo_actual["pregunta"]
        texto_pregunta = TEXTOS_PREGUNTA[clave]
        archivo_pregunta = AUDIO_PREGUNTA[clave]

        mostrar_texto_largo("PREGUNTA", texto_pregunta)
        reproducir_wav(archivo_pregunta)

        respuesta = escuchar_si_no(UMBRAL)

        if respuesta is None:
            mostrar("NO ESCUCHE", "Intenta de", "nuevo...")
            reproducir_wav("no_escuche.wav")
            time.sleep(1)
            continue  # Repite la misma pregunta

        nodo_actual = nodo_actual[respuesta]

    animal = nodo_actual["animal"]
    enviar_telemetria_gui("GANADOR", animal)
    mostrar("RESULTADO", "Creo que es:", animal)
    reproducir_wav(AUDIO_RESULTADO[animal])

    time.sleep(3)
    mostrar("FIN", "Gracias por", "jugar!")

except KeyboardInterrupt:
    audio_in.deinit()
    audio_out.deinit()
    mostrar("SISTEMA", "APAGADO", "")

except Exception as e:
    audio_in.deinit()
    audio_out.deinit()
    mostrar("ERROR", str(e))
