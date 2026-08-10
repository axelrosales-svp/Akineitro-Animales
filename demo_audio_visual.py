from machine import Pin, I2S, SoftI2C
import time
import math
import struct
import ssd1306

print("=== Iniciando Demo de Audio y Pantalla OLED (Ruta: quiz_bot_recorder) ===")

# ==========================================
# 1. DEFINICIÓN DE PINES
# ==========================================
OLED_SDA = 8
OLED_SCL = 9

MIC_SCK = 15
MIC_WS = 7
MIC_SD = 16

AMP_SCK = 5
AMP_WS = 4
AMP_SD = 6

# ==========================================
# 2. INICIALIZACIÓN DE PANTALLA OLED
# ==========================================
try:
    i2c = SoftI2C(sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=400000)
    oled = ssd1306.SSD1306_I2C(128, 64, i2c)
    oled.fill(0)
    oled.text("Iniciando...", 20, 25)
    oled.show()
    print("OLED inicializada.")
except Exception as e:
    print("Error al iniciar OLED:", e)
    oled = None

# ==========================================
# 3. INICIALIZACIÓN DE BOCINA Y SONIDO DE INICIO
# ==========================================
try:
    audio_out = I2S(
        0,
        sck=Pin(AMP_SCK),
        ws=Pin(AMP_WS),
        sd=Pin(AMP_SD),
        mode=I2S.TX,
        bits=16,
        format=I2S.MONO,
        rate=16000,
        ibuf=4096
    )
    print("Bocina I2S inicializada en pines 5, 4 y 6.")
    
    if oled:
        oled.fill(0)
        oled.text("Reproduciendo", 10, 20)
        oled.text("Tono de Inicio...", 10, 35)
        oled.show()

    # Generar un sonido de inicio estilo "Sci-Fi" ascendente
    rate = 16000
    duration_ms = 500
    num_samples = int(rate * duration_ms / 1000)
    sound_data = bytearray(num_samples * 2)
    
    for i in range(num_samples):
        # Frecuencia sube de 200Hz a 1000Hz de forma exponencial
        t = i / num_samples
        freq = 200 + 800 * (t ** 2)
        val = int(6000 * math.sin(2 * math.pi * freq * i / rate))
        struct.pack_into("<h", sound_data, i * 2, val)
        
    print("Sonando...")
    audio_out.write(sound_data)
    time.sleep_ms(600)  # Esperar a que termine de sonar
    
    audio_out.deinit()
    print("Tono de inicio reproducido con éxito.")
except Exception as e:
    print("Error en bocina:", e)

# ==========================================
# 4. INICIALIZACIÓN DE MICRÓFONO Y VISUALIZADOR
# ==========================================
try:
    audio_in = I2S(
        1,
        sck=Pin(MIC_SCK),
        ws=Pin(MIC_WS),
        sd=Pin(MIC_SD),
        mode=I2S.RX,
        bits=32,
        format=I2S.STEREO,
        rate=16000,
        ibuf=8000
    )
    print("Micrófono I2S inicializado en pines 15, 7 y 16.")
except Exception as e:
    print("Error en micrófono:", e)
    audio_in = None

# ==========================================
# 5. BUCLE DEL OSCILOSCOPIO EN PANTALLA OLED
# ==========================================
if oled and audio_in:
    print("\n--- ¡Bucle de Osciloscopio Activo! ---")
    print("Habla frente al micrófono para ver la forma de tu voz en la pantalla.")
    
    avg_val = 0.0
    alpha = 0.05
    max_seen = 200000.0
    buf = bytearray(1024)
    
    fin_tiempo = time.time() + 20
    while time.time() < fin_tiempo:
        audio_in.readinto(buf)
        
        muestras_y = []
        for i in range(0, len(buf), 8):
            val = int.from_bytes(buf[i:i+4], "little")
            if val > 2147483647:
                val -= 4294967296
                
            avg_val = (1 - alpha) * avg_val + alpha * val
            ac_val = val - avg_val
            
            abs_val = abs(ac_val)
            if abs_val > max_seen:
                max_seen = abs_val
            else:
                max_seen = max(20000.0, max_seen * 0.995)
                
            norm = ac_val / max_seen
            y = int(32 + norm * 20)
            y = max(10, min(54, y))
            muestras_y.append(y)
            
        oled.fill(0)
        oled.rect(0, 0, 128, 64, 1)
        oled.text("OSCILOSCOPIO VOZ", 4, 2)
        oled.hline(0, 56, 128, 1)
        
        for x in range(1, len(muestras_y)):
            oled.line(x-1, muestras_y[x-1], x, muestras_y[x], 1)
            
        oled.show()
        
    audio_in.deinit()
    oled.fill(0)
    oled.text("Demo Finalizada", 5, 20)
    oled.text("Presiona Play", 5, 35)
    oled.show()
    print("Demo finalizada. Micrófono liberado.")
else:
    print("No se pudo iniciar la demo porque falta la pantalla o el micrófono.")
