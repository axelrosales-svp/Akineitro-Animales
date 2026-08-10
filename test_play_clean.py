from machine import Pin, I2S
import time
import gc

# Configurar bocina en Mono nativo (idéntico a la demo que sonó limpio)
audio_out = I2S(
    0,
    sck=Pin(5),
    ws=Pin(4),
    sd=Pin(6),
    mode=I2S.TX,
    bits=16,
    format=I2S.MONO,
    rate=16000,
    ibuf=16384  # Búfer DMA grande
)

@micropython.viper
def scale_mono_volume(buf: ptr16, num_samples: int, vol_fp: int):
    """Escala muestras de 16 bits con signo convirtiendo correctamente la lectura sin signo de Viper."""
    i = 0
    while i < num_samples:
        val_un = int(buf[i])
        
        # Convertir de 16-bit sin signo (0-65535) a con signo (-32768 a 32767)
        if val_un > 32767:
            s16 = val_un - 65536
        else:
            s16 = val_un
            
        # Aplicar el volumen (vol_fp / 256)
        val = (s16 * vol_fp) >> 8
        
        # Limitar rango a 16-bit con signo
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
            
        # Convertir de vuelta a sin signo para escribir en el bytearray
        if val < 0:
            val += 65536
            
        buf[i] = val
        i += 1

def reproducir_archivo(ruta, vol_fp=35):
    print(f"=== Reproduciendo con Corrección de Signo: {ruta} ===")
    gc.collect()
    
    # Búfer de lectura
    buf = bytearray(8192)
    
    try:
        with open(ruta, "rb") as f:
            f.seek(44)  # Saltar cabecera WAV
            while True:
                bytes_read = f.readinto(buf)
                if bytes_read == 0:
                    break
                
                num_muestras = bytes_read // 2
                # Escalar volumen directamente con la conversión correcta de signo
                scale_mono_volume(buf, num_muestras, vol_fp)
                
                # Escribir a bocina
                audio_out.write(buf[:bytes_read])
                
        print("Fin de reproducción.")
    except Exception as e:
        print("Error:", e)

# Probar reproducir
reproducir_archivo("/audios_akinator/bienvenida.wav", vol_fp=75) # Volumen aumentado a 29% seguro
time.sleep(1)

audio_out.deinit()
