# grabadora_test.py
# ---------------------
# Script de prueba para ESP32-S3 (MicroPython)
# Graba tu voz por el micrófono (usando modo estéreo para evitar interferencias)
# y la reproduce de inmediato por la bocina con volumen controlado por potenciómetro.

import time
import machine
import micropython
import network
import array
from machine import Pin, I2C, I2S, ADC

# ==========================================
# 0. APAGAR WIFI PARA EVITAR RUIDO RF
# ==========================================
try:
    wlan_ap = network.WLAN(network.AP_IF)
    wlan_ap.active(False)
    wlan_sta = network.WLAN(network.STA_IF)
    wlan_sta.active(False)
    print("Wi-Fi desactivado para un audio limpio.")
except Exception as e:
    print("Error apagando Wi-Fi:", e)

# ==========================================
# 1. CONFIGURACIÓN DE PINES
# ==========================================
OLED_SCL_PIN = 9
OLED_SDA_PIN = 8

MIC_SCK_PIN = 15
MIC_WS_PIN = 7
MIC_SD_PIN = 16

AMP_SCK_PIN = 5
AMP_WS_PIN = 4
AMP_SD_PIN = 6

POT_PIN = 2
BUTTON_PIN = 0

# ==========================================
# 2. PARÁMETROS DE AUDIO Y CANAL
# ==========================================
SAMPLE_RATE = 16000
RECORD_DURATION = 3  # Segundos a grabar
NUM_SAMPLES = SAMPLE_RATE * RECORD_DURATION  # 48000 muestras
CHUNK_SAMPLES = 512

# CANAL DE MICRÓFONO:
# 0 = Canal Izquierdo (Left) -> Si el pin L/R del INMP441 está a GND (Recomendado).
# 1 = Canal Derecho (Right) -> Si el pin L/R está a VCC.
MIC_CHANNEL = 0 

# GANANCIA DEL MICRÓFONO:
# 14 = Ganancia fuerte de 4x (Recomendado para oírse bien de lejos).
# 15 = Ganancia media de 2x.
MIC_SHIFT = 14

# Buffer de grabación pre-asignado en 16 bits (96,000 bytes)
RECORD_BUFFER = bytearray(NUM_SAMPLES * 2)

# ==========================================
# 3. INICIALIZACIÓN DE COMPONENTES
# ==========================================
button = Pin(BUTTON_PIN, Pin.IN, Pin.PULL_UP)

pot = ADC(Pin(POT_PIN))
pot.atten(ADC.ATTN_11DB)

oled = None
oled_connected = False
try:
    import ssd1306
    i2c = I2C(0, scl=Pin(OLED_SCL_PIN), sda=Pin(OLED_SDA_PIN))
    oled = ssd1306.SSD1306_I2C(128, 64, i2c)
    oled_connected = True
    print("Pantalla OLED inicializada.")
except Exception as e:
    print("Advertencia: No se pudo inicializar la pantalla OLED.")

# Inicialización única de I2S1 RX (Micrófono) en ESTÉREO
audio_in = I2S(
    1,
    sck=Pin(MIC_SCK_PIN),
    ws=Pin(MIC_WS_PIN),
    sd=Pin(MIC_SD_PIN),
    mode=I2S.RX,
    bits=32,
    format=I2S.STEREO,
    rate=SAMPLE_RATE,
    ibuf=8000
)

# Inicialización única de I2S0 TX (Bocina) en MONO
audio_out = I2S(
    0,
    sck=Pin(AMP_SCK_PIN),
    ws=Pin(AMP_WS_PIN),
    sd=Pin(AMP_SD_PIN),
    mode=I2S.TX,
    bits=16,
    format=I2S.MONO,
    rate=SAMPLE_RATE,
    ibuf=8000
)

prev_pot_val = 0

# ==========================================
# 4. FUNCIONES EN CÓDIGO NATIVO (VIPER)
# ==========================================

@micropython.viper
def convert_stereo_32_to_mono_16(src_32: ptr32, dest_16: ptr16, offset: int, length: int, channel: int, shift: int):
    """
    Toma las muestras estéreo de 32 bits del mic, extrae únicamente la señal
    del canal seleccionado (0 = Izquierdo, 1 = Derecho) y las guarda como 16 bits.
    """
    i = 0
    while i < length:
        # Extrae el canal deseado (Left o Right)
        s32 = src_32[(i << 1) + channel]
        
        # Desplazamiento para aplicar ganancia
        s16 = s32 >> shift
        
        if s16 > 32767:
            s16 = 32767
        elif s16 < -32768:
            s16 = -32768
            
        dest_16[offset + i] = s16
        i += 1

@micropython.viper
def clean_audio_offline(buf: ptr16, length: int):
    """
    Filtro DSP Offline en bloque:
    1. Calcula y remueve el offset DC de la grabación.
    2. Aplica un filtro Paso Bajo (LPF) gentil para suavizar el siseo.
    """
    i = 0
    total = 0
    while i < length:
        total += int(buf[i])
        i += 1
    avg = total // length
    
    i = 0
    prev = 0
    while i < length:
        s = int(buf[i]) - avg
        s_filtered = (prev + s) >> 1
        prev = s_filtered
        buf[i] = s_filtered
        i += 1

@micropython.viper
def scale_playback_chunk(src_16: ptr16, dest_16: ptr16, offset: int, length: int, vol_fp: int):
    """Aplica el volumen en tiempo real sobre las muestras de audio."""
    i = 0
    while i < length:
        s16 = src_16[offset + i]
        val = (s16 * vol_fp) >> 8
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
        dest_16[i] = val
        i += 1

# ==========================================
# 5. FUNCIONES DE CONTROL E INTERFAZ
# ==========================================

def update_display(state_text, vol_text=""):
    """Dibuja en el OLED. Sólo se llama cuando no hay audio activo."""
    if not oled_connected:
        print(f"[{state_text}] Vol: {vol_text}")
        return

    try:
        oled.fill(0)
        oled.text("=== PRUEBA MIC ===", 4, 0)
        if vol_text:
            oled.text(f"Vol: {vol_text}", 4, 16)
        oled.text(state_text, 4, 34)
        oled.show()
    except Exception as e:
        print(f"Error OLED: {e}")

def read_volume_smoothed():
    """Lee el potenciómetro y filtra el ruido del ADC."""
    global prev_pot_val
    total = 0
    for _ in range(4):
        total += pot.read()
    avg = total // 4
    
    if abs(avg - prev_pot_val) > 25:
        prev_pot_val = avg
        
    vol_multiplier = 0.5 + (prev_pot_val / 4095.0) * 4.5
    vol_fp = 128 + (prev_pot_val * 1152) // 4095
    
    return f"{vol_multiplier:.1f}x", vol_fp

# ==========================================
# 6. BUCLE PRINCIPAL
# ==========================================

print("--- Grabadora Test Listo (Estéreo + Viper) ---")
print("Presiona el botón BOOT (GPIO 0) para grabar 3 segundos de tu voz.")

temp_in_buf = bytearray(CHUNK_SAMPLES * 8)   # Buffer estéreo 32 bits (4096 bytes)
temp_out_buf = bytearray(CHUNK_SAMPLES * 2)  # Buffer mono 16 bits (1024 bytes)
last_display_time = 0

while True:
    vol_str, vol_fp = read_volume_smoothed()
    
    # Actualizar pantalla de espera
    current_time = time.ticks_ms()
    if time.ticks_diff(current_time, last_display_time) > 250:
        update_display("Presiona BOOT", vol_str)
        last_display_time = current_time

    # Al presionar BOOT
    if button.value() == 0:
        time.sleep_ms(150)  # Antirrebote
        
        # 1. GRABACIÓN
        print("Grabando...")
        update_display(">> GRABANDO <<", vol_str)
        time.sleep_ms(50)  # Dejar bus I2C libre
        
        # Vaciar buffer del mic de muestras viejas
        try:
            for _ in range(25):
                audio_in.readinto(temp_in_buf)
        except:
            pass
            
        sample_idx = 0
        while sample_idx < NUM_SAMPLES:
            bytes_read = audio_in.readinto(temp_in_buf)
            if bytes_read > 0:
                samples_read = bytes_read // 8
                # Extracción y conversión nativa
                convert_stereo_32_to_mono_16(
                    temp_in_buf,
                    RECORD_BUFFER,
                    sample_idx,
                    samples_read,
                    MIC_CHANNEL,
                    MIC_SHIFT
                )
                sample_idx += samples_read
                
        print("Grabación finalizada. Procesando...")
        update_display("Procesando...", vol_str)
        time.sleep_ms(50)
        
        # 2. PROCESAMIENTO DSP
        clean_audio_offline(RECORD_BUFFER, NUM_SAMPLES)
        time.sleep_ms(50)

        # 3. REPRODUCCIÓN
        print("Reproduciendo...")
        update_display("<< REPRODUC. >>", vol_str)
        time.sleep_ms(50)  # Dejar bus I2C libre
        
        # Limpiar canal de salida
        try:
            for _ in range(4):
                audio_out.write(temp_out_buf)
        except:
            pass
            
        play_idx = 0
        while play_idx < NUM_SAMPLES:
            vol_str_play, vol_fp_play = read_volume_smoothed()
            
            rem = NUM_SAMPLES - play_idx
            chunk_len = CHUNK_SAMPLES if rem > CHUNK_SAMPLES else rem
            
            scale_playback_chunk(RECORD_BUFFER, temp_out_buf, play_idx, chunk_len, vol_fp_play)
            audio_out.write(temp_out_buf)
            play_idx += chunk_len
            
        print("Reproducción terminada.")
        update_display("¡LISTO!", vol_str)
        time.sleep(1)
        last_display_time = time.ticks_ms()
        
    time.sleep_ms(50)  # Descanso de CPU para evitar bloqueos de Thonny
