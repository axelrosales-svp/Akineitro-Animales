from machine import Pin, SoftI2C
import time
import ssd1306

print("=== Escáner y Prueba Simple de OLED (Pines: 41, 42) ===")

# Inicializar I2C
try:
    # Probar conexión estándar
    i2c = SoftI2C(sda=Pin(41), scl=Pin(42), freq=100000)
    dispositivos = i2c.scan()
    print("Dispositivos detectados en el bus I2C:", [hex(d) for d in dispositivos])
    
    if 0x3c in dispositivos or 60 in dispositivos:
        oled = ssd1306.SSD1306_I2C(128, 64, i2c)
        oled.fill(0)
        oled.text("=== OLED OK ===", 10, 10)
        oled.text("¡Conexion exitosa!", 5, 30)
        oled.text("Pines: 41 y 42", 10, 50)
        oled.show()
        print("-> ¡Pantalla encendida con éxito!")
    else:
        print("-> Error: No se detectó ninguna pantalla en la dirección 0x3C.")
        print("Verifica si el cable VCC (3.3V) o GND se soltaron al quitar el potenciómetro.")
        
except Exception as e:
    print("Error I2C:", e)
    print("Esto ocurre si hay un falso contacto en los cables de datos (SDA/SCL) o si están invertidos.")
