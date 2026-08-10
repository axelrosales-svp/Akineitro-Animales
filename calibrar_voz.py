from machine import Pin, I2S, SoftI2C
import time
import math
import struct
import json
import gc
import array
import ssd1306

print("=== Sistema de Calibración de Pico de Frecuencia (SI / NO) ===")

# ==========================================
# 1. HARDWARE
# ==========================================
OLED_SDA = 8
OLED_SCL = 9
MIC_SCK = 15
MIC_WS = 7
MIC_SD = 16
AMP_SCK = 5
AMP_WS = 4
AMP_SD = 6

try:
    i2c = SoftI2C(sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=100000)
    oled = ssd1306.SSD1306_I2C(128, 64, i2c)
except Exception:
    oled = None

audio_in = I2S(
    1, sck=Pin(MIC_SCK), ws=Pin(MIC_WS), sd=Pin(MIC_SD),
    mode=I2S.RX, bits=32, format=I2S.STEREO, rate=16000, ibuf=8000
)

# ==========================================
# 2. MENSAJES Y TONOS
# ==========================================
def mostrar(linea1, linea2=""):
    if oled:
        try:
            oled.fill(0)
            oled.text("== CALIBRACION ==", 4, 2)
            oled.hline(0, 12, 128, 1)
            oled.text(linea1, 0, 24)
            oled.text(linea2, 0, 42)
            oled.show()
        except Exception:
            pass
    print(f"[{linea1}] {linea2}")

def emitir_beep(frecuencia=800, duracion_ms=150):
    try:
        audio_out = I2S(
            0, sck=Pin(AMP_SCK), ws=Pin(AMP_WS), sd=Pin(AMP_SD),
            mode=I2S.TX, bits=16, format=I2S.MONO, rate=16000, ibuf=2048
        )
        rate = 16000
        num_samples = int(rate * duracion_ms / 1000)
        sound_data = bytearray(num_samples * 2)
        for i in range(num_samples):
            val = int(5000 * math.sin(2 * math.pi * frecuencia * i / rate))
            struct.pack_into("<h", sound_data, i * 2, val)
        audio_out.write(sound_data)
        time.sleep_ms(duracion_ms + 50)
        audio_out.deinit()
    except Exception:
        pass

# ==========================================
# 3. DSP ACELERADO (Viper)
# ==========================================
@micropython.viper
def analyze_block_dsp(src_32: ptr32, length: int, channel: int, shift_val: int) -> int:
    """
    Calcula el promedio (DC offset) y luego obtiene el ZCR y la energía de alta frecuencia.
    Retorna (hi_energy << 16) | zcr.
    """
    # 1. Promedio DC
    i = 0
    total = 0
    while i < length:
        s32 = src_32[(i << 1) + channel]
        s16 = s32 >> shift_val
        if s16 > 32767:
            s16 = 32767
        elif s16 < -32768:
            s16 = -32768
        total += s16
        i += 1
    avg = total // length
    
    # 2. ZCR y Energía paso alto (diferencias)
    i = 0
    zcr = 0
    hi_energy = 0
    prev_val = 0
    gate_threshold = 800
    
    while i < length:
        s32 = src_32[(i << 1) + channel]
        s16 = s32 >> shift_val
        if s16 > 32767:
            s16 = 32767
        elif s16 < -32768:
            s16 = -32768
            
        s16_clean = s16 - avg
        amp = s16_clean
        if amp < 0:
            amp = -amp
            
        if amp > gate_threshold:
            # Cruce por cero
            if (prev_val >= 0 and s16_clean < 0) or (prev_val < 0 and s16_clean >= 0):
                zcr += 1
                
            # Filtro Paso Alto (Diferencia)
            diff = s16_clean - prev_val
            if diff < 0:
                hi_energy += -diff
            else:
                hi_energy += diff
                
        prev_val = s16_clean
        i += 1
        
    return (hi_energy << 16) | zcr

@micropython.viper
def get_block_mav(src_32: ptr32, length: int, channel: int, shift_val: int) -> int:
    i = 0
    total = 0
    while i < length:
        s32 = src_32[(i << 1) + channel]
        s16 = s32 >> shift_val
        if s16 > 32767:
            s16 = 32767
        elif s16 < -32768:
            s16 = -32768
        total += s16
        i += 1
    avg = total // length
    
    i = 0
    sum_abs = 0
    while i < length:
        s32 = src_32[(i << 1) + channel]
        s16 = s32 >> shift_val
        if s16 > 32767:
            s16 = 32767
        elif s16 < -32768:
            s16 = -32768
        amp = s16 - avg
        if amp < 0:
            sum_abs += -amp
        else:
            sum_abs += amp
        i += 1
    return sum_abs // length

# ==========================================
# 4. CAPTURA DE AUDIO Y ANÁLISIS DE VENTANA MÁXIMA
# ==========================================
def grabar_y_obtener_picos(umbral_energia):
    flush = bytearray(1600)
    for _ in range(10):
        audio_in.readinto(flush)
        
    # Grabamos durante 1.0 segundo (16,000 muestras) en bloques de 100ms
    temp_buf = bytearray(1600 * 8) # Buffer para 100ms
    
    pico_zcr = 0
    pico_hi_energy = 0
    
    hablando = False
    inicio = time.time()
    
    # 1. Espera activa VAD
    while time.time() - inicio < 4:
        num_bytes = audio_in.readinto(temp_buf)
        if num_bytes > 0:
            samples_read = num_bytes // 8
            mav = get_block_mav(temp_buf, samples_read, 0, 14)
            if mav > umbral_energia:
                hablando = True
                # Procesar el primer bloque que activó la voz
                res = analyze_block_dsp(temp_buf, samples_read, 0, 14)
                pico_zcr = res & 0xFFFF
                pico_hi_energy = res >> 16
                break
        time.sleep_ms(2)
        
    if not hablando:
        return None
        
    # 2. Grabar los siguientes 8 bloques de 100ms cada uno (800ms restantes)
    for _ in range(8):
        num_bytes = audio_in.readinto(temp_buf)
        if num_bytes > 0:
            samples_read = num_bytes // 8
            res = analyze_block_dsp(temp_buf, samples_read, 0, 14)
            zcr = res & 0xFFFF
            hi_energy = res >> 16
            
            # Buscar el pico máximo (la sibilancia de la "S" será un pico clarísimo)
            if zcr > pico_zcr:
                pico_zcr = zcr
            if hi_energy > pico_hi_energy:
                pico_hi_energy = hi_energy
                
    # Convertir a porcentajes/escalas relativas estables
    # pico_zcr representa el máximo de cruces en 1600 muestras (100ms)
    zcr_percent = (pico_zcr / 1600) * 100
    hi_scaled = pico_hi_energy / 1600
    
    return zcr_percent, hi_scaled

def calibrar_ruido_inicial():
    mostrar("CALIBRANDO RUIDO", "Guarda silencio")
    time.sleep(0.5)
    lecturas = []
    temp_buf = bytearray(1024)
    for _ in range(15):
        num_bytes = audio_in.readinto(temp_buf)
        if num_bytes > 0:
            mav = get_block_mav(temp_buf, num_bytes // 8, 0, 14)
            lecturas.append(mav)
        time.sleep(0.1)
    ruido_base = sum(lecturas) // len(lecturas) if lecturas else 100
    return max(ruido_base * 3, 500)

# ==========================================
# 5. BUCLE DE CALIBRACIÓN
# ==========================================
mostrar("Iniciando...", "Espera")
time.sleep(1)

umbral_ruido = calibrar_ruido_inicial()
print(f"Umbral MAV para VAD: {umbral_ruido}")

muestras_si_zcr = []
muestras_si_hi = []
muestras_no_zcr = []
muestras_no_hi = []

# Calibrar SÍ
j = 0
while j < 5:
    emitir_beep(600, 100)
    time.sleep_ms(300)
    mostrar(f"Di 'SI' ({j+1}/5)", "¡HABLA AHORA!")
    emitir_beep(800, 150)
    
    res = grabar_y_obtener_picos(umbral_ruido)
    if res is None:
        mostrar("NO ESCUCHE", "Repitiendo intento")
        emitir_beep(400, 400)
        time.sleep(1.5)
        continue
        
    zcr, hi = res
    muestras_si_zcr.append(zcr)
    muestras_si_hi.append(hi)
    mostrar("¡Guardada!", f"P-ZCR:{zcr:.1f}% P-HI:{hi:.1f}")
    j += 1
    time.sleep(1.8)

# Calibrar NO
j = 0
while j < 5:
    emitir_beep(600, 100)
    time.sleep_ms(300)
    mostrar(f"Di 'NO' ({j+1}/5)", "¡HABLA AHORA!")
    emitir_beep(800, 150)
    
    res = grabar_y_obtener_picos(umbral_ruido)
    if res is None:
        mostrar("NO ESCUCHE", "Repitiendo intento")
        emitir_beep(400, 400)
        time.sleep(1.5)
        continue
        
    zcr, hi = res
    muestras_no_zcr.append(zcr)
    muestras_no_hi.append(hi)
    mostrar("¡Guardada!", f"P-ZCR:{zcr:.1f}% P-HI:{hi:.1f}")
    j += 1
    time.sleep(1.8)

# ==========================================
# 6. CALCULAR PERFILES DE PICO
# ==========================================
centroid_si_zcr = sum(muestras_si_zcr) / len(muestras_si_zcr)
centroid_si_hi = sum(muestras_si_hi) / len(muestras_si_hi)

centroid_no_zcr = sum(muestras_no_zcr) / len(muestras_no_zcr)
centroid_no_hi = sum(muestras_no_hi) / len(muestras_no_hi)

print("\n=== Centroides de Pico de Frecuencia ===")
print(f"SI ➡ ZCR Pico: {centroid_si_zcr:.2f}%, Energía Pico: {centroid_si_hi:.2f}")
print(f"NO ➡ ZCR Pico: {centroid_no_zcr:.2f}%, Energía Pico: {centroid_no_hi:.2f}")

config = {
    "si_zcr": centroid_si_zcr,
    "si_hlr": centroid_si_hi,  # Mapeado a si_hlr para compatibilidad
    "no_zcr": centroid_no_zcr,
    "no_hlr": centroid_no_hi   # Mapeado a no_hlr para compatibilidad
}

try:
    with open("config_voz.json", "w") as f:
        json.dump(config, f)
    mostrar("¡CALIBRADO!", "Perfil guardado")
    print("Configuración guardada en '/config_voz.json'.")
except Exception as e:
    mostrar("Error al guardar", str(e))

audio_in.deinit()
