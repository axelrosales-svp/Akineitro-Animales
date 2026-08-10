# akinator_animales.py
# ---------------------
# Versión optimizada con procesamiento espectral de voz en dos dimensiones.
# Clasifica las palabras "SI" y "NO" comparando las características
# de la voz con los perfiles calibrados del usuario.

import struct
import time
import os
import sys
import gc
import json
import math
import array
import micropython
import network
from machine import I2S, Pin, SoftI2C, ADC
import ssd1306

gc.collect()

# Apagar WiFi para evitar ruido de fondo electromagnético
try:
    network.WLAN(network.AP_IF).active(False)
    network.WLAN(network.STA_IF).active(False)
except:
    pass

# ==========================================
# 1. CONFIGURACIÓN DE PINES Y HARDWARE
# ==========================================
OLED_SDA = 8
OLED_SCL = 9
MIC_SCK = 15
MIC_WS = 7
MIC_SD = 16
AMP_SCK = 5
AMP_WS = 4
AMP_SD = 6

i2c = None
oled = None
try:
    i2c = SoftI2C(scl=Pin(OLED_SCL), sda=Pin(OLED_SDA), freq=100000)
    oled = ssd1306.SSD1306_I2C(128, 64, i2c)
except:
    pass

# Micrófono I2S
audio_in = I2S(
    1, sck=Pin(MIC_SCK), ws=Pin(MIC_WS), sd=Pin(MIC_SD),
    mode=I2S.RX, bits=32, format=I2S.STEREO, rate=16000, ibuf=8000
)

# Bocina I2S
audio_out = I2S(
    0, sck=Pin(AMP_SCK), ws=Pin(AMP_WS), sd=Pin(AMP_SD),
    mode=I2S.TX, bits=16, format=I2S.MONO, rate=16000, ibuf=8192
)

# ==========================================
# 2. CONFIGURACIÓN DE AUDIO Y PERFILES DE VOZ
# ==========================================
MIC_CHANNEL = 0
MIC_SHIFT = 14

MONO_READ_BUF = bytearray(4096)
MIC_FLUSH_BUF = bytearray(1600)
DSP_MIC_BUF = bytearray(1024)

# Array para resultados DSP acelerados: [zcr, hi_energy, lo_energy]
dsp_results = array.array('i', [0, 0, 0])

# Cargar centroides de calibración de voz
si_zcr, si_hlr = 12.0, 8.0  # Valores por defecto para SÍ
no_zcr, no_hlr = 4.0, 2.0   # Valores por defecto para NO

try:
    with open("config_voz.json", "r") as f:
        config = json.load(f)
        si_zcr = config["si_zcr"]
        si_hlr = config["si_hlr"]
        no_zcr = config["no_zcr"]
        no_hlr = config["no_hlr"]
    print("Perfiles de calibración de voz cargados:")
    print(f"  SI ➡ ZCR: {si_zcr:.1f}%, HLR: {si_hlr:.1f}")
    print(f"  NO ➡ ZCR: {no_zcr:.1f}%, HLR: {no_hlr:.1f}")
except Exception:
    print("No se encontró 'config_voz.json'. Usando configuración de respaldo.")

# ==========================================
# 3. INTERFAZ GRÁFICA Y PANTALLA
# ==========================================
def enviar_telemetria_gui(evento, detalle=""):
    sys.stdout.write(f"[GUI_EVENT]:{evento}:{detalle}\n")

def mostrar(titulo, linea1="", linea2=""):
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
def scale_mono_volume(buf: ptr16, num_samples: int, vol_fp: int):
    i = 0
    while i < num_samples:
        val_un = int(buf[i])
        if val_un > 32767:
            s16 = val_un - 65536
        else:
            s16 = val_un
            
        val = (s16 * vol_fp) >> 8
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
            
        if val < 0:
            val += 65536
            
        buf[i] = val
        i += 1

@micropython.viper
def extract_dsp_features(src_32: ptr32, length: int, channel: int, shift_val: int, res: ptr32):
    i = 0
    zcr = 0
    hi_energy = 0
    lo_energy = 0
    prev_val = 0
    
    while i < length:
        s32 = src_32[(i << 1) + channel]
        s16 = s32 >> shift_val
        
        if s16 > 32767:
            s16 = 32767
        elif s16 < -32768:
            s16 = -32768
            
        # Cruces por cero
        if (prev_val >= 0 and s16 < 0) or (prev_val < 0 and s16 >= 0):
            zcr += 1
            
        # Filtro Paso Alto
        diff = s16 - prev_val
        if diff < 0:
            hi_energy += -diff
        else:
            hi_energy += diff
            
        # Filtro Paso Bajo
        summ = s16 + prev_val
        if summ < 0:
            lo_energy += -summ
        else:
            lo_energy += summ
            
        prev_val = s16
        i += 1
        
    res[0] = res[0] + zcr
    res[1] = res[1] + hi_energy
    res[2] = res[2] + lo_energy

@micropython.viper
def get_amplitude_only(src_32: ptr32, length: int, channel: int, shift_val: int) -> int:
    i = 0
    max_amp = 0
    while i < length:
        s32 = src_32[(i << 1) + channel]
        s16 = s32 >> shift_val
        amp = s16
        if amp < 0:
            amp = -amp
        if amp > max_amp:
            max_amp = amp
        i += 1
    return max_amp

# ==========================================
# 5. FUNCIONES DE REPRODUCCIÓN Y DETECCIÓN
# ==========================================
def leer_volumen_potenciometro_fp():
    return 105

def reproducir_wav(archivo):
    ruta_completa = "/audios_akinator/" + archivo
    enviar_telemetria_gui("AUDIO", archivo)
    gc.collect()
    try:
        vol_fp = leer_volumen_potenciometro_fp()
        with open(ruta_completa, "rb") as f:
            f.seek(44)
            while True:
                bytes_read = f.readinto(MONO_READ_BUF)
                if bytes_read == 0:
                    break
                num_muestras = bytes_read // 2
                scale_mono_volume(MONO_READ_BUF, num_muestras, vol_fp)
                audio_out.write(MONO_READ_BUF[:bytes_read])
        time.sleep_ms(400)
    except OSError:
        enviar_telemetria_gui("ERROR", f"No se encontro {ruta_completa}")
        time.sleep(1)

def vaciar_buffer_mic():
    try:
        for _ in range(30):
            audio_in.readinto(MIC_FLUSH_BUF)
    except:
        pass

def calibrar_ruido():
    mostrar("CALIBRANDO...", "Guarda silencio", "midiendo...")
    reproducir_wav("calibrando.wav")
    vaciar_buffer_mic()
    lecturas = []
    for _ in range(20):
        num_bytes = audio_in.readinto(DSP_MIC_BUF)
        if num_bytes > 0:
            amp = get_amplitude_only(DSP_MIC_BUF, num_bytes // 8, MIC_CHANNEL, MIC_SHIFT)
            lecturas.append(amp)
        time.sleep(0.1)
    ruido_base = sum(lecturas) // len(lecturas) if lecturas else 400
    umbral = max(ruido_base * 3, 2000)
    mostrar("LISTO", "Prepara tu voz", "")
    time.sleep(1)
    return umbral

def escuchar_si_no(umbral_energia):
    vaciar_buffer_mic()
    enviar_telemetria_gui("ESTADO", "ESCUCHANDO")

    inicio_escucha = time.time()
    hablando = False
    
    # Reiniciar acumuladores dsp
    dsp_results[0] = 0
    dsp_results[1] = 0
    dsp_results[2] = 0
    
    total_muestras = 0
    ultimo_segundo_mostrado = -1

    while time.time() - inicio_escucha < 5:
        transcurrido = int(time.time() - inicio_escucha)
        segundos_restantes = 5 - transcurrido

        if not hablando and segundos_restantes != ultimo_segundo_mostrado:
            ultimo_segundo_mostrado = segundos_restantes
            mostrar("RESPONDE AHORA", f"Tiempo: {segundos_restantes}s", "Di SI o NO")
            enviar_telemetria_gui("RELOJ", str(segundos_restantes))

        num_bytes = audio_in.readinto(DSP_MIC_BUF)
        if num_bytes > 0:
            samples_read = num_bytes // 8
            
            # Si no ha empezado a hablar, solo monitorear la amplitud
            if not hablando:
                amp = get_amplitude_only(DSP_MIC_BUF, samples_read, MIC_CHANNEL, MIC_SHIFT)
                if amp > umbral_energia:
                    hablando = True
                    dsp_results[0] = 0
                    dsp_results[1] = 0
                    dsp_results[2] = 0
                    total_muestras = 0
                    mostrar("PROCESANDO...", "Sigue hablando")
                    print("-> Analizando entrada de voz...")

            if hablando:
                # Extraer ZCR, energía alta y baja simultáneamente
                extract_dsp_features(DSP_MIC_BUF, samples_read, MIC_CHANNEL, MIC_SHIFT, dsp_results)
                total_muestras += samples_read

                # Analizar ventana de 1.2 segundos (19,200 muestras)
                if total_muestras >= 19200:
                    zcr_percent = (dsp_results[0] / total_muestras) * 100
                    hi = float(dsp_results[1])
                    lo = float(dsp_results[2])
                    hlr_ratio = (hi / lo * 10.0) if lo > 0 else 0.0
                    
                    print(f"-> Características leídas ➡ ZCR: {zcr_percent:.1f}%, HLR: {hlr_ratio:.1f}")

                    # Clasificador de Distancia Mínima en Espacio Bidimensional
                    dist_si = math.sqrt((zcr_percent - si_zcr)**2 + (hlr_ratio - si_hlr)**2)
                    dist_no = math.sqrt((zcr_percent - no_zcr)**2 + (hlr_ratio - no_hlr)**2)
                    
                    print(f"-> Distancia a SI: {dist_si:.1f} | Distancia a NO: {dist_no:.1f}")

                    if dist_si < dist_no:
                        mostrar("RESPUESTA:", "SI")
                        enviar_telemetria_gui("RESPUESTA", "SI")
                        time.sleep(1.5)
                        return "si"
                    else:
                        mostrar("RESPUESTA:", "NO")
                        enviar_telemetria_gui("RESPUESTA", "NO")
                        time.sleep(1.5)
                        return "no"

        time.sleep_ms(2)

    enviar_telemetria_gui("RESPUESTA", "TIMEOUT")
    return None

# ==========================================
# 6. ARBOL DE DECISIÓN
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
# 7. BUCLE PRINCIPAL DEL JUEGO
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
            continue

        nodo_actual = nodo_actual[respuesta]

    animal = nodo_actual["animal"]
    enviar_telemetria_gui("GANADOR", animal)
    mostrar("RESULTADO", "Creo que es:", animal)
    reproducir_wav(AUDIO_RESULTADO[animal])

    time.sleep(3)
    mostrar("FIN", "Gracias por", "jugar!")

except KeyboardInterrupt:
    mostrar("SISTEMA", "APAGADO", "")
finally:
    audio_in.deinit()
    audio_out.deinit()
