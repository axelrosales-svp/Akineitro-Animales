from machine import Pin, I2S, SoftI2C
import time
import math
import struct
import json
import gc
import ssd1306

print("=== Sistema de Calibración de Voz (SI / NO) ===")

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
# 2. FUNCIONES DE MENSAJES Y BEEP
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
# 3. PROCESAMIENTO DSP (VIPER)
# ==========================================
@micropython.viper
def convert_and_extract_features(src_32: ptr32, length: int, channel: int) -> int:
    zcr = 0
    prev_val = 0
    i = 0
    shift_val = 14
    
    while i < length:
        s32 = src_32[(i << 1) + channel]
        s16 = s32 >> shift_val
        
        if s16 > 32767:
            s16 = 32767
        elif s16 < -32768:
            s16 = -32768
            
        if (prev_val >= 0 and s16 < 0) or (prev_val < 0 and s16 >= 0):
            zcr += 1
            
        prev_val = s16
        i += 1
    return zcr

def grabar_muestra():
    flush = bytearray(1600)
    for _ in range(10):
        audio_in.readinto(flush)
        
    duracion_muestras = 19200
    temp_buf = bytearray(1024)
    total_zcr = 0
    muestras_leidas = 0
    
    while muestras_leidas < duracion_muestras:
        bytes_read = audio_in.readinto(temp_buf)
        if bytes_read > 0:
            samples_read = bytes_read // 8
            zcr = convert_and_extract_features(temp_buf, samples_read, 0)
            total_zcr += zcr
            muestras_leidas += samples_read
            
    return (total_zcr / muestras_leidas) * 100

# ==========================================
# 4. CAPTURA DE SEÑAL
# ==========================================
mostrar("Preparando...", "Espera")
time.sleep(1)

muestras_si = []
muestras_no = []

for j in range(4):
    emitir_beep(600, 100)
    time.sleep_ms(300)
    mostrar(f"Di 'SI' (Muestra {j+1}/4)", "¡AHORA!")
    emitir_beep(800, 150)
    
    zcr = grabar_muestra()
    muestras_si.append(zcr)
    
    mostrar("¡Guardada!", f"Frec: {zcr:.1f}%")
    time.sleep(2)

for j in range(4):
    emitir_beep(600, 100)
    time.sleep_ms(300)
    mostrar(f"Di 'NO' (Muestra {j+1}/4)", "¡AHORA!")
    emitir_beep(800, 150)
    
    zcr = grabar_muestra()
    muestras_no.append(zcr)
    
    mostrar("¡Guardada!", f"Frec: {zcr:.1f}%")
    time.sleep(2)

# ==========================================
# 5. AJUSTE DE CONFIGURACIÓN
# ==========================================
promedio_si = sum(muestras_si) / len(muestras_si)
promedio_no = sum(muestras_no) / len(muestras_no)
umbral_optimo = (promedio_si + promedio_no) / 2

print("\n=== Resultados de Calibración ===")
print(f"Frecuencia (ZCR) de 'SI': {muestras_si}")
print(f"Frecuencia (ZCR) de 'NO': {muestras_no}")
print(f"Promedio SI: {promedio_si:.2f}%")
print(f"Promedio NO: {promedio_no:.2f}%")
print(f"Umbral de corte: {umbral_optimo:.2f}%")

config = {
    "umbral_zcr": umbral_optimo,
    "promedio_si": promedio_si,
    "promedio_no": promedio_no
}

try:
    with open("config_voz.json", "w") as f:
        json.dump(config, f)
    mostrar("¡CALIBRADO!", f"Corte: {umbral_optimo:.1f}%")
    print("Configuración guardada en 'config_voz.json'.")
except Exception as e:
    mostrar("Error al guardar", str(e))

audio_in.deinit()
