from machine import Pin, SoftI2C
import time

print("=== Escáner Universal de I2C (Buscando Pantalla en cualquier Pin) ===")

# Lista de todos los pines GPIO razonables en el ESP32-S3
PINES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 21, 38, 39, 40, 41, 42, 45, 47, 48]

encontrado = False

# Probamos todas las parejas de pines contiguos o comunes
candidatos = []
# 1. Agregar parejas comunes
candidatos.extend([
    (41, 42), (42, 41),
    (8, 9), (9, 8),
    (1, 2), (2, 1),
    (17, 18), (18, 17)
])

# 2. Agregar cualquier par de pines en el rango
for sda in PINES:
    for scl in PINES:
        if sda == scl:
            continue
        if (sda, scl) not in candidatos:
            candidatos.append((sda, scl))

print(f"Escaneando {len(candidatos)} combinaciones de pines...")

for sda_pin, scl_pin in candidatos:
    try:
        # Usamos SoftI2C con un timeout corto para que sea rápido
        i2c = SoftI2C(sda=Pin(sda_pin), scl=Pin(scl_pin), freq=100000)
        dispositivos = i2c.scan()
        
        # Un dispositivo I2C real (como la pantalla 0x3C) devolverá su dirección
        # Excluimos lecturas ruidosas (si devuelve más de 3 direcciones falsas)
        if len(dispositivos) > 0 and len(dispositivos) <= 2:
            print(f"\n-> ¡DISPOSITIVO ENCONTRADO!")
            print(f"Pines: SDA = GPIO {sda_pin} | SCL = GPIO {scl_pin}")
            print(f"Dirección I2C: {[hex(d) for d in dispositivos]}")
            encontrado = True
            break
    except Exception:
        pass

if not encontrado:
    print("\nNo se detectó ningún dispositivo I2C en ningún pin.")
    print("Por favor, revisa físicamente que los cables de la pantalla (especialmente SDA y SCL) estén firmes y no se hayan soltado al desconectar el potenciómetro.")
