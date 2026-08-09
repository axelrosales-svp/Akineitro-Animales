# akinator_gui.py
# ---------------------
# Interfaz Gráfica (GUI) Futurista de Escritorio para Akinator ESP32-S3.
# Incluye reloj regresivo en tiempo real de 5 a 1 segundo para hablar.

import sys
import time
import json
import queue
import threading
import serial
import serial.tools.list_ports
import tkinter as tk
from tkinter import ttk

# ==========================================
# CONFIGURACIÓN ESTÉTICA Y COLORES (DARK NEON)
# ==========================================
BG_MAIN = "#0f172a"      # Azul noche oscuro
BG_CARD = "#1e293b"      # Card container
ACCENT_BLUE = "#38bdf8"  # Neon Cyan
ACCENT_GREEN = "#22c55e" # Neon Green
ACCENT_RED = "#ef4444"   # Neon Red
TEXT_LIGHT = "#f8fafc"   # Blanco puro
TEXT_MUTED = "#64748b"   # Gris deshabilitado

# ==========================================
# MAPA DE ANIMALES Y REGLAS DE DESCARTE
# ==========================================
ANIMALES = [
    {"id": "Perro", "nombre": "Perro", "icono": "🐶", "desc": "Mascota que ladra"},
    {"id": "Cerdo", "nombre": "Cerdo", "icono": "🐷", "desc": "Hocico plano, cola rosa"},
    {"id": "Burro", "nombre": "Burro", "icono": "🫏", "desc": "Animal de carga"},
    {"id": "Conejo", "nombre": "Conejo", "icono": "🐰", "desc": "Orejas largas y saltos"},
    {"id": "Leon", "nombre": "León", "icono": "🦁", "desc": "Rey felino de la sabana"},
    {"id": "Panda", "nombre": "Panda", "icono": "🐼", "desc": "Blanco y negro, bambú"},
    {"id": "Oso", "nombre": "Oso", "icono": "🐻", "desc": "Depredador del bosque"}
]

class AkinatorGUIApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🤖 AKINATOR ROBOTICS - ESP32-S3 CONTROLLER")
        self.root.geometry("1100x750")
        self.root.configure(bg=BG_MAIN)
        
        self.serial_port = None
        self.running = True
        self.msg_queue = queue.Queue()
        
        self.card_widgets = {}
        self.build_ui()
        
        self.root.after(50, self.poll_queue)
        self.start_serial_thread()
        
    def build_ui(self):
        # 1. HEADER
        header_frame = tk.Frame(self.root, bg=BG_MAIN)
        header_frame.pack(fill="x", padx=20, pady=15)
        
        lbl_title = tk.Label(
            header_frame, 
            text="🤖 AKINATOR ROBOTICS - INTERFAZ DE CONTROL", 
            font=("Helvetica", 20, "bold"), 
            fg=ACCENT_BLUE, 
            bg=BG_MAIN
        )
        lbl_title.pack(side="left")
        
        self.lbl_status = tk.Label(
            header_frame, 
            text="Buscando puerto COM...", 
            font=("Helvetica", 10, "bold"), 
            fg=ACCENT_BLUE, 
            bg=BG_MAIN
        )
        self.lbl_status.pack(side="right")

        # 2. CONTENEDOR PRINCIPAL (2 COLUMNAS)
        main_container = tk.Frame(self.root, bg=BG_MAIN)
        main_container.pack(fill="both", expand=True, padx=20, pady=10)
        
        # COLUMNA IZQUIERDA: PANTALLA OLED VIRTUAL & ESTADO
        left_col = tk.Frame(main_container, bg=BG_MAIN, width=380)
        left_col.pack(side="left", fill="y", padx=(0, 15))
        left_col.pack_propagate(False)
        
        # OLED MIRROR FRAME
        oled_box = tk.LabelFrame(left_col, text=" 📱 PANTALLA OLED DEL ESP32 ", font=("Helvetica", 11, "bold"), fg=ACCENT_BLUE, bg=BG_CARD, bd=2)
        oled_box.pack(fill="x", pady=(0, 15))
        
        self.oled_screen = tk.Frame(oled_box, bg="#051014", width=340, height=180)
        self.oled_screen.pack(padx=10, pady=10, fill="both")
        
        self.oled_title = tk.Label(self.oled_screen, text="=== VOZ AI ===", font=("Courier", 14, "bold"), fg="#38bdf8", bg="#051014")
        self.oled_title.pack(pady=(10, 5))
        
        self.oled_line1 = tk.Label(self.oled_screen, text="AKINATOR", font=("Courier", 12), fg="#f8fafc", bg="#051014")
        self.oled_line1.pack(pady=2)
        
        self.oled_line2 = tk.Label(self.oled_screen, text="Iniciando...", font=("Courier", 12), fg="#f8fafc", bg="#051014")
        self.oled_line2.pack(pady=2)
        
        # CUENTA REGRESIVA & HABLA INDICADOR
        timer_box = tk.LabelFrame(left_col, text=" 🎤 ESTADO DE ESCUCHA ", font=("Helvetica", 11, "bold"), fg=ACCENT_BLUE, bg=BG_CARD, bd=2)
        timer_box.pack(fill="x", pady=10)
        
        self.lbl_mic_status = tk.Label(timer_box, text="ESPERANDO INICIO...", font=("Helvetica", 13, "bold"), fg=TEXT_MUTED, bg=BG_CARD)
        self.lbl_mic_status.pack(pady=15)
        
        self.progress_bar = ttk.Progressbar(timer_box, orient="horizontal", mode="determinate", length=300)
        self.progress_bar.pack(pady=(0, 15), padx=15)

        # LOG DE TELEMETRÍA
        log_box = tk.LabelFrame(left_col, text=" 📋 LOG DE TELEMETRÍA ", font=("Helvetica", 10, "bold"), fg=TEXT_MUTED, bg=BG_CARD, bd=1)
        log_box.pack(fill="both", expand=True, pady=(10, 0))
        
        self.log_text = tk.Text(log_box, bg="#020617", fg="#94a3b8", font=("Consolas", 9), bd=0)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

        # COLUMNA DERECHA: TARJETAS DE LOS 7 ANIMALES
        right_col = tk.LabelFrame(main_container, text=" 🐾 CANDIDATOS RESTANTES (7 ANIMALES) ", font=("Helvetica", 12, "bold"), fg=ACCENT_BLUE, bg=BG_MAIN, bd=2)
        right_col.pack(side="right", fill="both", expand=True)

        self.cards_frame = tk.Frame(right_col, bg=BG_MAIN)
        self.cards_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.render_animal_cards()

    def render_animal_cards(self):
        for idx, animal in enumerate(ANIMALES):
            row = idx // 3
            col = idx % 3
            
            card = tk.Frame(self.cards_frame, bg=BG_CARD, bd=2, relief="groove")
            card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
            self.cards_frame.grid_columnconfigure(col, weight=1)
            self.cards_frame.grid_rowconfigure(row, weight=1)
            
            lbl_icon = tk.Label(card, text=animal["icono"], font=("Segoe UI Emoji", 36), bg=BG_CARD)
            lbl_icon.pack(pady=(10, 2))
            
            lbl_name = tk.Label(card, text=animal["nombre"], font=("Helvetica", 14, "bold"), fg=TEXT_LIGHT, bg=BG_CARD)
            lbl_name.pack(pady=2)
            
            lbl_desc = tk.Label(card, text=animal["desc"], font=("Helvetica", 9), fg=TEXT_MUTED, bg=BG_CARD)
            lbl_desc.pack(pady=(0, 10))
            
            self.card_widgets[animal["id"]] = {
                "card": card,
                "icon": lbl_icon,
                "name": lbl_name,
                "desc": lbl_desc
            }

    def actualizar_oled_virtual(self, titulo, l1="", l2=""):
        self.oled_title.config(text=f"=== {titulo} ===")
        self.oled_line1.config(text=l1)
        self.oled_line2.config(text=l2)

    def log_event(self, text):
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    def descartar_animal(self, animal_id):
        if animal_id in self.card_widgets:
            w = self.card_widgets[animal_id]
            w["card"].config(bg="#020617")
            w["icon"].config(bg="#020617")
            w["name"].config(fg=TEXT_MUTED, bg="#020617")
            w["desc"].config(fg="#334155", bg="#020617")

    def resaltar_ganador(self, animal_id):
        for aid, w in self.card_widgets.items():
            if aid == animal_id:
                w["card"].config(bg="#15803d", bd=4, relief="ridge")
                w["icon"].config(bg="#15803d")
                w["name"].config(fg="#ffffff", bg="#15803d", font=("Helvetica", 16, "bold"))
                w["desc"].config(fg="#bbf7d0", bg="#15803d")
            else:
                self.descartar_animal(aid)

    def start_serial_thread(self):
        t = threading.Thread(target=self.serial_loop, daemon=True)
        t.start()

    def serial_loop(self):
        target_port = "COM8"
        
        while self.running:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            active_port = target_port if target_port in ports else (ports[0] if ports else None)
            
            if not active_port:
                self.msg_queue.put(("STATUS_ERR", "⚠️ ESP32 no detectado en USB"))
                time.sleep(2)
                continue
                
            try:
                ser = serial.Serial(active_port, baudrate=115200, timeout=1)
                ser.dtr = True
                ser.rts = True
                self.msg_queue.put(("STATUS", f"🟢 Conectado en {active_port}"))
                
                while self.running and ser.is_open:
                    if ser.in_waiting > 0:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            self.msg_queue.put(("LINE", line))
                    time.sleep(0.01)
                    
            except serial.SerialException:
                self.msg_queue.put(("STATUS_ERR", "⚠️ COM8 ocupado. Oprime el botón ROJO de STOP en Thonny."))
                time.sleep(2)
            except Exception as e:
                self.msg_queue.put(("STATUS_ERR", f"Reconectando COM..."))
                time.sleep(2)

    def poll_queue(self):
        try:
            while not self.msg_queue.empty():
                msg_type, content = self.msg_queue.get_nowait()
                
                if msg_type == "STATUS":
                    self.lbl_status.config(text=content, fg=ACCENT_GREEN)
                elif msg_type == "STATUS_ERR":
                    self.lbl_status.config(text=content, fg=ACCENT_RED)
                elif msg_type == "LINE":
                    self.process_serial_line(content)
        finally:
            self.root.after(50, self.poll_queue)

    def process_serial_line(self, line):
        self.log_event(line)
        
        if "[GUI_EVENT]:" in line:
            parts = line.split("[GUI_EVENT]:")[1].split(":")
            evento = parts[0]
            detalle = parts[1] if len(parts) > 1 else ""
            
            if evento == "OLED":
                sub = detalle.split("|")
                t = sub[0] if len(sub) > 0 else ""
                l1 = sub[1] if len(sub) > 1 else ""
                l2 = sub[2] if len(sub) > 2 else ""
                self.actualizar_oled_virtual(t, l1, l2)
                
            elif evento == "RELOJ":
                sec = int(detalle) if detalle.isdigit() else 5
                self.lbl_mic_status.config(text=f"🎙️ ¡DI SI O NO! ({sec}s)", fg=ACCENT_GREEN)
                self.progress_bar["value"] = (sec / 5.0) * 100
                
            elif evento == "ESTADO":
                if detalle == "ESCUCHANDO":
                    self.lbl_mic_status.config(text="🎙️ ¡DI SI O NO! (5s)", fg=ACCENT_GREEN)
                    self.progress_bar["value"] = 100
                elif detalle == "GRABANDO":
                    self.lbl_mic_status.config(text="⏳ GRABANDO TU VOZ...", fg=ACCENT_BLUE)
                    
            elif evento == "GANADOR":
                self.resaltar_ganador(detalle)

if __name__ == "__main__":
    root = tk.Tk()
    app = AkinatorGUIApp(root)
    root.mainloop()
