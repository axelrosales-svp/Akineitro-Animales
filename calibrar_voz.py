from machine import Pin, I2S, SoftI2C
import time
import math
import struct
import json
import gc
import ssd1306

print("=== Sistema de Calibración Bidimensional de Voz (SI / NO) ===")

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

# Array para guardar resultados acumulados de Viper: [zcr, hi_energy, lo_energy]
dsp_results = array.array('i', [0, 0, 0])

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
# 3. DSP ACELERADO (Extracción de 2 características)
# ==========================================
@micropython.viper
def extract_dsp_features(src_32: ptr32, length: int, channel: int, shift_val: int, res: ptr32):
    """
    Calcula simultáneamente:
    1. Cruces por cero (ZCR)
    2. Energía de alta frecuencia (Filtro paso alto: diferencia)
    3. Energía de baja frecuencia (Filtro paso bajo: suma)
    """
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
            
        # Energía alta (Diferencia absoluta)
        diff = s16 - prev_val
        if diff < 0:
            hi_energy += -diff
        else:
            hi_energy += diff
            
        # Energía baja (Suma absoluta)
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

def grabar_muestra():
    # Vaciar buffer
    flush = bytearray(1600)
    for _ in range(10):
        audio_in.readinto(flush)
        
    # Grabamos durante 1.2 segundos (19,200 muestras)
    duracion_muestras = 19200
    temp_buf = bytearray(1024)
    
    # Reiniciar acumuladores
    dsp_results[0] = 0
    dsp_results[1] = 0
    dsp_results[2] = 0
    
    muestras_leidas = 0
    while muestras_leidas < duracion_muestras:
        bytes_read = audio_in.readinto(temp_buf)
        if bytes_read > 0:
            samples_read = bytes_read // 8
            extract_dsp_features(temp_buf, samples_read, 0, 14, dsp_results)
            muestras_leidas += samples_read
            
    # Calcular características finales
    zcr_percent = (dsp_results[0] / muestras_leidas) * 100
    
    hi = float(dsp_results[1])
    lo = float(dsp_results[2])
    # Ratio alta/baja frecuencia (HLR) escalada por 10 para igualar pesos en el espacio euclidiano
    hlr_ratio = (hi / lo * 10.0) if lo > 0 else 0.0
    
    return zcr_percent, hlr_ratio

# ==========================================
# 4. CAPTURA DE CALIBRACIÓN (5 veces cada palabra)
# ==========================================
mostrar("Preparando...", "Espera")
time.sleep(1)

muestras_si_zcr = []
muestras_si_hlr = []
muestras_no_zcr = []
muestras_no_hlr = []

# Calibración para SÍ
for j in range(5):
    emitir_beep(600, 100)
    time.sleep_ms(300)
    mostrar(f"Di 'SI' (Muestra {j+1}/5)", "¡AHORA!")
    emitir_beep(800, 150)
    
    zcr, hlr = grabar_muestra()
    muestras_si_zcr.append(zcr)
    muestras_si_hlr.append(hlr)
    
    mostrar("¡Guardada!", f"ZCR:{zcr:.1f}% H:{hlr:.1f}")
    time.sleep(1.8)

# Calibración para NO
for j in range(5):
    emitir_beep(600, 100)
    time.sleep_ms(300)
    mostrar(f"Di 'NO' (Muestra {j+1}/5)", "¡AHORA!")
    emitir_beep(800, 150)
    
    zcr, hlr = grabar_muestra()
    muestras_no_zcr.append(zcr)
    muestras_no_hlr.append(hlr)
    
    mostrar("¡Guardada!", f"ZCR:{zcr:.1f}% H:{hlr:.1f}")
    time.sleep(1.8)

# ==========================================
# 5. CALCULO DE CENTROIDES (Modelo de Distancia Mínima)
# ==========================================
centroid_si_zcr = sum(muestras_si_zcr) / len(muestras_si_zcr)
centroid_si_hlr = sum(muestras_si_hlr) / len(muestras_si_hlr)

centroid_no_zcr = sum(muestras_no_zcr) / len(muestras_no_zcr)
centroid_no_hlr = sum(muestras_no_hlr) / len(muestras_no_hlr)

print("\n=== Centroides Calculados ===")
print(f"Centroide 'SI' ➡ ZCR: {centroid_si_zcr:.2f}%, HLR: {centroid_si_hlr:.2f}")
print(f"Centroide 'NO' ➡ ZCR: {centroid_no_zcr:.2f}%, HLR: {centroid_no_hlr:.2f}")

config = {
    "si_zcr": centroid_si_zcr,
    "si_hlr": centroid_si_hlr,
    "no_zcr": centroid_no_zcr,
    "no_hlr": centroid_no_hlr
}

try:
    with open("config_voz.json", "w") as f:
        json.dump(config, f)
    mostrar("¡CALIBRADO!", "Datos guardados")
    print("Configuración guardada en 'config_voz.json'.")
except Exception as e:
    mostrar("Error al guardar", str(e))

audio_in.deinit()
