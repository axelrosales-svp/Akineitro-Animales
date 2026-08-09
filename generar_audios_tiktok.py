# generar_audios_tiktok.py
# -----------------------------
# Genera los 16 archivos de audio WAV en formato 16000Hz 16-bit Mono PCM
# usando la voz neural de TikTok (es-MX-JorgeNeural) de Microsoft Edge.

import os
import wave
import asyncio
import edge_tts
import miniaudio

VOICE = "es-MX-JorgeNeural"
OUTPUT_DIR = "audios_akinator"

AUDIOS = {
    "bienvenida.wav": "Hola. Piensa en un animal de la lista: León, panda, oso, conejo, cerdo, perro o burro. Presiona el botón BOOT para empezar.",
    "calibrando.wav": "Calibrando ruido del ambiente. Por favor guarda silencio.",
    "no_escuche.wav": "No te escuché bien. Por favor responde sí o no.",
    "p_domestico.wav": "Es un animal doméstico o de granja?",
    "p_ladra.wav": "Es una mascota que ladra y es el mejor amigo del hombre?",
    "p_hocico.wav": "Tiene hocico plano y cola rizada?",
    "p_orejas.wav": "Tiene orejas largas y se mueve a saltos?",
    "p_melena.wav": "Es un felino de la sabana y el macho tiene melena?",
    "p_bambu.wav": "Es blanco y negro y come bambú?",
    "r_perro.wav": "El animal en el que estabas pensando es un Perro.",
    "r_cerdo.wav": "El animal en el que estabas pensando es un Cerdo.",
    "r_burro.wav": "El animal en el que estabas pensando es un Burro.",
    "r_conejo.wav": "El animal en el que estabas pensando es un Conejo.",
    "r_leon.wav": "El animal en el que estabas pensando es un León.",
    "r_panda.wav": "El animal en el que estabas pensando es un Panda.",
    "r_oso.wav": "El animal en el que estabas pensando es un Oso."
}

async def generar_audio(filename, text):
    temp_mp3 = "temp.mp3"
    print(f"Generando {filename} -> '{text[:30]}...'")
    
    # 1. Generar MP3 con voz de TikTok
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(temp_mp3)
    
    # 2. Decodificar MP3 a PCM 16000Hz Mono 16-bit
    sound = miniaudio.decode_file(
        temp_mp3, 
        output_format=miniaudio.SampleFormat.SIGNED16, 
        nchannels=1, 
        sample_rate=16000
    )
    
    # 3. Guardar como WAV estandar
    out_path = os.path.join(OUTPUT_DIR, filename)
    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(1)      # Mono
        wf.setsampwidth(2)      # 16-bit
        wf.setframerate(16000)  # 16000Hz
        wf.writeframes(bytes(sound.samples))
        
    if os.path.exists(temp_mp3):
        os.remove(temp_mp3)

async def main():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        
    print("--- Generando los 16 audios con voz de TikTok ---")
    for filename, text in AUDIOS.items():
        await generar_audio(filename, text)
    print("--- ¡TODOS LOS AUDIOS FUERON GENERADOS CON ÉXITO! ---")

if __name__ == "__main__":
    asyncio.run(main())
