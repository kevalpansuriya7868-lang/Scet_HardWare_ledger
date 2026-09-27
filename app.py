import os
import sys
import json
import re
import math
import random
import socket
import smtplib
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime
import io
import base64
import customtkinter as ctk
import auth_utils
from tkinter import ttk, messagebox, simpledialog, filedialog, Canvas
from PIL import Image
import oracledb
import requests

# PDF Generation Libraries
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

DB_CONFIG = config.get("oracle", {})  # Student installs won't have this key — see config.student.json
SECURITY_CONFIG = config.get("security", {})
SMTP_CONFIG = config.get("smtp", {})

# Used only by the STUDENT role, which never touches Oracle directly.
# It calls student_api.py over the internet instead. Admin role is untouched
# and still connects to Oracle directly via get_db_connection() below.
STUDENT_API_CONFIG = config.get("student_api", {"base_url": "http://127.0.0.1:5000"})
STUDENT_API_BASE = STUDENT_API_CONFIG.get("base_url", "http://127.0.0.1:5000")
STUDENT_API_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Auto-start the Student Catalog API (student_api.py) inside THIS process,
# on a background thread, so admins never have to open a second terminal.
#
# Rules:
#   - Only runs on full admin/server installs (config.json has an "oracle"
#     section). A student-only distributable has no Oracle to serve from,
#     so there is nothing to auto-start.
#   - Skips silently if something is already answering on 127.0.0.1:5000
#     (e.g. you started student_api.py manually, or it's already running
#     as its own long-lived process/service) — never double-binds the port.
#   - Only ever binds 127.0.0.1 — a Cloudflare Tunnel (or similar) still
#     points at this same local port to expose it to students off-campus,
#     exactly like running it standalone did.
# ---------------------------------------------------------------------------
LOCAL_STUDENT_API_URL = "http://127.0.0.1:5000"


def _student_api_already_running(url=LOCAL_STUDENT_API_URL, timeout=1.5):
    try:
        r = requests.get(f"{url}/api/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False

def start_student_api_in_background():
    if "oracle" not in config:
        print("[student_api] No 'oracle' section in config.json (student-only "
              "build) — nothing to auto-start.")
        return

    if _student_api_already_running():
        print("[student_api] Already running on 127.0.0.1:5000 — skipping auto-start.")
        return

    try:
        import student_api  # Defines student_api.app. Does NOT call app.run()
                             # itself — that only happens under student_api.py's
                             # own `if __name__ == "__main__":` guard.
    except Exception as e:
        print(f"[student_api] Could not import student_api.py — auto-start skipped: {e}")
        print("[student_api] Student catalog mode will show a connection error "
              "until this is fixed and the app is restarted.")
        return

    def _run_flask():
        try:
            student_api.app.run(
                host="127.0.0.1",
                port=5000,
                debug=False,
                use_reloader=False,   # Reloader would try to re-exec this script — must stay off when embedded.
                threaded=True,        # Let it handle a few students' requests concurrently.
            )
        except Exception as e:
            print(f"[student_api] Background server stopped unexpectedly: {e}")

    threading.Thread(target=_run_flask, daemon=True, name="student-api-thread").start()

    # Give Flask a moment to bind, so the very first student login right
    # after launch doesn't race the server startup.
    for _ in range(20):  # up to ~5 seconds
        if _student_api_already_running(timeout=0.5):
            print("[student_api] Auto-started successfully on 127.0.0.1:5000.")
            return
        time.sleep(0.25)

    print("[student_api] Started, but it hasn't answered its health check yet — "
          "it may still be binding, or check the console for errors above.")


THEME = {
    "bg_main": "#F1F5F9",
    "card_bg": "#FFFFFF",
    "card_border": "#D5DEE7",
    "card_highlight": "#F8FAFC",
    "input_bg": "#F8FAFC",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "hero_gradient": "#2563EB",
    "hero_gradient_dark": "#1D4ED8",
    "accent_gold": "#D97706",
    "accent_gold_hover": "#B45309",
    "accent_green": "#10B981",
    "accent_green_hover": "#059669",
    "accent_red": "#EF4444",
    "accent_red_hover": "#DC2626",
    "text_main": "#0F172A",
    "text_muted": "#64748B"
}

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")


class FullAnimatedMeshCanvas(Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, highlightthickness=0, bd=0, **kwargs)
        self.step = 0
        self.colors = [
            (255, 99, 71),
            (164, 169, 242),
            (193, 255, 226),
            (83, 209, 57)
        ]
        self._anim_job = None
        self.bind("<Destroy>", self._on_destroy)
        self.animate_mesh()

    def _on_destroy(self, event=None):
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    def interpolate_color(self, c1, c2, factor):
        return tuple(int(c1[i] + (c2[i] - c1[i]) * factor) for i in range(3))

    def animate_mesh(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return

        w = self.winfo_width() or 1400
        h = self.winfo_height() or 820

        try:
            self.delete("all")

            t1 = (math.sin(self.step * 0.02) + 1) / 2
            t2 = (math.cos(self.step * 0.025) + 1) / 2

            c_a = self.interpolate_color(self.colors[0], self.colors[1], t1)
            c_b = self.interpolate_color(self.colors[2], self.colors[3], t2)

            lines = 16
            for i in range(lines):
                factor = i / lines
                cur_c = self.interpolate_color(c_a, c_b, factor)
                soft_r = int(cur_c[0] * 0.2 + 204)
                soft_g = int(cur_c[1] * 0.2 + 204)
                soft_b = int(cur_c[2] * 0.2 + 204)
                hex_cur = f"#{soft_r:02x}{soft_g:02x}{soft_b:02x}"
                y0 = i * (h / lines)
                y1 = (i + 1) * (h / lines)
                self.create_rectangle(0, y0, w, y1, fill=hex_cur, outline="")

            b1_x = (w * 0.25) + math.sin(self.step * 0.025) * (w * 0.15)
            b1_y = (h * 0.35) + math.cos(self.step * 0.02) * (h * 0.15)
            self.create_oval(
                b1_x - 220, b1_y - 220, b1_x + 220, b1_y + 220,
                fill="#FED7AA", outline=""
            )

            b2_x = (w * 0.75) + math.cos(self.step * 0.03) * (w * 0.2)
            b2_y = (h * 0.65) + math.sin(self.step * 0.025) * (h * 0.15)
            self.create_oval(
                b2_x - 250, b2_y - 250, b2_x + 250, b2_y + 250,
                fill="#DDD6FE", outline=""
            )

            self.step += 1
            self._anim_job = self.after(45, self.animate_mesh)
        except Exception:
            return


class SCETLabManager(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("SCET Lab Component & Issue Tracking System")
        self.geometry("1400x820")
        self.configure(fg_color=THEME["bg_main"])

        self.current_user = None
        self.current_role = None
        self._login_attempts = {}  # username -> list of recent failed-attempt timestamps

        self.selected_branch = ctk.StringVar(value="")

        self.is_student_mode = False
        self.animating_flip = False
        self.show_only_overdue = False

        self.active_edit_issue_id = None
        self.current_history_tab = None
        self.current_history_query = None

        self.ensure_image_schema()
        self.load_branding_images()
        self.apply_table_styling()
        self.show_login_screen()

    def get_db_connection(self):
        return oracledb.connect(
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            service_name=DB_CONFIG["service_name"]
        )

    def ensure_image_schema(self):
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = 'COMPONENT_IMAGES'")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    CREATE TABLE component_images (
                        image_id NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY PRIMARY KEY,
                        comp_id VARCHAR2(50) NOT NULL,
                        branch_code VARCHAR2(50) NOT NULL,
                        image_data BLOB NOT NULL
                    )
                """)
                conn.commit()

                cursor.execute("""
                    SELECT COUNT(*) FROM user_tab_cols 
                    WHERE table_name = 'LAB_COMPONENTS' AND column_name = 'IMAGE_DATA'
                """)
                if cursor.fetchone()[0] > 0:
                    cursor.execute("SELECT comp_id, branch_code, image_data FROM lab_components WHERE image_data IS NOT NULL")
                    for r_cid, r_bcode, r_blob in cursor.fetchall():
                        if r_blob:
                            raw = r_blob.read() if hasattr(r_blob, 'read') else r_blob
                            cursor.execute("INSERT INTO component_images (comp_id, branch_code, image_data) VALUES (:1, :2, :3)", (r_cid, r_bcode, raw))
                    conn.commit()
            cursor.close()
            conn.close()
        except Exception:
            pass

    def get_component_images(self, comp_id, branch_code):
        if self.current_role == "student":
            try:
                resp = requests.get(
                    f"{STUDENT_API_BASE}/api/branches/{branch_code}/components/{comp_id}/images",
                    timeout=STUDENT_API_TIMEOUT
                )
                resp.raise_for_status()
                return [base64.b64decode(b64_str) for b64_str in resp.json()]
            except Exception:
                return []

        images = []
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT image_data FROM component_images WHERE comp_id = :1 AND branch_code = :2 ORDER BY image_id ASC", (comp_id, branch_code))
            for row in cursor.fetchall():
                blob = row[0]
                if blob:
                    images.append(blob.read() if hasattr(blob, 'read') else blob)
            cursor.close()
            conn.close()
        except Exception:
            pass
        return images

    def load_branding_images(self):
        self.su_logo = None
        self.scet_logo = None
        self.su_logo_header = None
        self.scet_logo_header = None

        self.su_path = os.path.join(BASE_DIR, "sesuni.png")
        self.scet_path = os.path.join(BASE_DIR, "logo-footer.png")

        if os.path.exists(self.su_path):
            try:
                pil_su = Image.open(self.su_path)
                self.su_logo = ctk.CTkImage(light_image=pil_su, dark_image=pil_su, size=(90, 90))
                self.su_logo_header = ctk.CTkImage(light_image=pil_su, dark_image=pil_su, size=(48, 48))
            except Exception:
                pass

        if os.path.exists(self.scet_path):
            try:
                pil_scet = Image.open(self.scet_path)
                self.scet_logo = ctk.CTkImage(light_image=pil_scet, dark_image=pil_scet, size=(90, 90))
                self.scet_logo_header = ctk.CTkImage(light_image=pil_scet, dark_image=pil_scet, size=(48, 48))
            except Exception:
                pass

    def get_local_ips(self):
        ips = ["127.0.0.1"]
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            active_ip = s.getsockname()[0]
            s.close()
            if active_ip not in ips:
                ips.append(active_ip)
        except Exception:
            try:
                hostname = socket.gethostname()
                for ip in socket.gethostbyname_ex(hostname)[2]:
                    if ip not in ips:
                        ips.append(ip)
            except Exception:
                pass
        return ips

    def verify_scet_network(self):
        if not SECURITY_CONFIG.get("enforce_scet_subnet", False):
            return True

        allowed = SECURITY_CONFIG.get("allowed_subnets", [])
        current_ips = self.get_local_ips()

        for ip in current_ips:
            for prefix in allowed:
                if ip.startswith(prefix) or ip == prefix:
                    return True

        messagebox.showerror(
            "Access Denied (Network Policy)",
            f"Unauthorized Network: {', '.join(current_ips)}\n\nThis application is strictly restricted to SCET Campus Wi-Fi / LAN."
        )
        return False

    def reindex_issue_ids(self):
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT issue_id FROM issue_records ORDER BY issue_id ASC")
            all_ids = [r[0] for r in cursor.fetchall()]

            for idx, old_id in enumerate(all_ids):
                cursor.execute("UPDATE issue_records SET issue_id = :1 WHERE issue_id = :2", (-(idx + 1000000), old_id))

            for idx, old_id in enumerate(all_ids, start=1):
                temp_id = -(idx - 1 + 1000000)
                cursor.execute("UPDATE issue_records SET issue_id = :1 WHERE issue_id = :2", (idx, temp_id))

            conn.commit()
            cursor.close()
            conn.close()
        except Exception:
            pass

    def send_raw_email(self, recipient_email, subject, body_text, pdf_bytes=None, pdf_filename="GatePass.pdf"):
        if not SMTP_CONFIG.get("enabled", False) or not recipient_email:
            return False, "SMTP email delivery is disabled in config.json."

        try:
            msg = MIMEMultipart()
            msg["From"] = SMTP_CONFIG.get("sender_email")
            msg["To"] = recipient_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body_text, "plain"))

            if pdf_bytes:
                part = MIMEApplication(pdf_bytes, Name=pdf_filename)
                part['Content-Disposition'] = f'attachment; filename="{pdf_filename}"'
                msg.attach(part)

            server = smtplib.SMTP(SMTP_CONFIG.get("host", "smtp.gmail.com"), SMTP_CONFIG.get("port", 587))
            server.starttls()
            server.login(SMTP_CONFIG.get("sender_email"), SMTP_CONFIG.get("sender_password"))
            server.sendmail(msg["From"], recipient_email, msg.as_string())
            server.quit()
            return True, "Email sent successfully."
        except Exception as e:
            return False, str(e)

    def send_async_email(self, recipient_email, subject, body_text, pdf_bytes=None, pdf_filename="GatePass.pdf"):
        threading.Thread(target=self.send_raw_email, args=(recipient_email, subject, body_text, pdf_bytes, pdf_filename), daemon=True).start()

    def send_otp_email(self, recipient_email, otp_code, branch_code):
        subject = f"SCET Lab Portal - Password Reset/Deletion OTP for Branch {branch_code}"
        body = (
            f"Hello Faculty Member,\n\n"
            f"A secure authorization request was initiated for Department/Branch: {branch_code}.\n"
            f"Your 6-Digit One-Time Password (OTP) is: {otp_code}\n\n"
            f"This OTP is strictly confidential. Do not share it.\n"
            f"Sarvajanik College of Engineering & Technology (SCET)"
        )
        return self.send_raw_email(recipient_email, subject, body)

    def build_pdf_header(self, title_text, badge_text):
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "TitleStyle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=13,
            alignment=1,
            textColor=colors.HexColor("#1D4ED8"),
            spaceAfter=2
        )
        sub_title_style = ParagraphStyle(
            "SubTitleStyle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            alignment=1,
            textColor=colors.HexColor("#334155"),
            spaceAfter=2
        )
        badge_style = ParagraphStyle(
            "BadgeStyle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            alignment=1,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=4
        )

        logo_left = ""
        logo_right = ""
        if hasattr(self, "su_path") and os.path.exists(self.su_path):
            try:
                logo_left = RLImage(self.su_path, width=54, height=54)
            except Exception:
                logo_left = ""

        if hasattr(self, "scet_path") and os.path.exists(self.scet_path):
            try:
                logo_right = RLImage(self.scet_path, width=54, height=54)
            except Exception:
                logo_right = ""

        center_text = [
            Paragraph("SARVAJANIK UNIVERSITY, SURAT", sub_title_style),
            Paragraph(title_text, title_style),
            Paragraph(badge_text, badge_style)
        ]

        header_table_data = [[logo_left, center_text, logo_right]]
        header_table = Table(header_table_data, colWidths=[65, 410, 65])
        header_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('PADDING', (0,0), (-1,-1), 0),
        ]))
        return [header_table, Spacer(1, 4), HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2563EB"), spaceAfter=10)]

    def generate_gate_pass_pdf_bytes(self, issue_id, enroll, sname, sbranch, smobile, semail, comp_id, qty, issue_time_str, return_time_str, status, issued_by):
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        elements = []

        elements.extend(self.build_pdf_header(
            "SARVAJANIK COLLEGE OF ENGINEERING & TECHNOLOGY (SCET)",
            "DEPARTMENT LABORATORY HARDWARE ISSUE & GATE PASS"
        ))

        pass_info = [
            [
                Paragraph(f"<b>PASS NO:</b> SCET-GP-{issue_id}", styles["Normal"]),
                Paragraph(f"<b>PRINT DATE:</b> {datetime.now().strftime('%d-%b-%Y %I:%M:%S %p')}", styles["Normal"])
            ]
        ]
        t_pass = Table(pass_info, colWidths=[270, 270])
        t_pass.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
            ('PADDING', (0,0), (-1,-1), 6),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ]))
        elements.append(t_pass)
        elements.append(Spacer(1, 10))

        sec_heading = ParagraphStyle("SecHead", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10, textColor=colors.HexColor("#1E293B"), spaceAfter=4)
        elements.append(Paragraph("1. STUDENT CREDENTIALS", sec_heading))

        student_data = [
            [f"Student Name: {sname}", f"Enrollment No: {enroll}"],
            [f"Branch / Department: {sbranch}", f"Contact No: {smobile}"],
            [f"Student Email: {semail or 'N/A'}", f"Department: {self.selected_branch.get()}"]
        ]
        t_student = Table(student_data, colWidths=[270, 270])
        t_student.setStyle(TableStyle([
            ('PADDING', (0,0), (-1,-1), 4),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
        ]))
        elements.append(t_student)
        elements.append(Spacer(1, 10))

        elements.append(Paragraph("2. ISSUANCE & HARDWARE DETAILS", sec_heading))
        hw_data = [
            ["COMPONENT CODE", "QTY", "ISSUED AT", "DUE AT", "STATUS", "AUTHORIZED BY"],
            [str(comp_id), f"{qty} Unit(s)", str(issue_time_str), str(return_time_str), str(status), str(issued_by)]
        ]
        t_hw = Table(hw_data, colWidths=[100, 50, 110, 110, 80, 90])
        t_hw.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2563EB")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8.5),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#94A3B8")),
            ('PADDING', (0,0), (-1,-1), 5),
        ]))
        elements.append(t_hw)
        elements.append(Spacer(1, 12))

        elements.append(Paragraph("3. UNDERTAKING & LAB POLICIES", sec_heading))
        policies = (
            "• The student is held strictly accountable for any burnt pins, physical damage, or loss of components.<br/>"
            "• Overdue returns will incur an academic penalty of ₹10 / day after scheduled return time.<br/>"
            "• This digital receipt serves as an authorized laboratory pass."
        )
        elements.append(Paragraph(policies, ParagraphStyle("Pol", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#475569"))))
        
        doc.build(elements)
        pdf_val = buffer.getvalue()
        buffer.close()
        return pdf_val

    def trigger_auto_issuance_email(self, issue_id, sname, enroll, sbranch, smobile, comp_id, qty, days, semail, issue_time_str, return_time_str):
        if not semail:
            return

        subject = f"Hardware Issued Receipt - SCET Lab [{comp_id}]"
        body = (
            f"Dear {sname},\n\n"
            f"Greetings from Sarvajanik College of Engineering & Technology (SCET)!\n\n"
            f"Your laboratory hardware checkout has been registered successfully. Here are your transaction details:\n\n"
            f"  • Transaction Pass : SCET-GP-{issue_id}\n"
            f"  • Student Name     : {sname}\n"
            f"  • Enrollment No    : {enroll}\n"
            f"  • Academic Branch  : {sbranch}\n"
            f"  • Component Issued : {comp_id} (Quantity: {qty} Unit(s))\n"
            f"  • Issuing Time     : {issue_time_str}\n"
            f"  • Return Due Time  : {return_time_str} ({days} Day(s))\n"
            f"  • Late Return Fine : ₹10 / day after scheduled due time\n\n"
            f"Please find your official Gate Pass attached as a PDF to this email.\n\n"
            f"Best Regards,\n"
            f"Laboratory In-Charge & Faculty Staff\n"
            f"Sarvajanik College of Engineering & Technology (SCET), Surat"
        )
        
        pdf_bytes = self.generate_gate_pass_pdf_bytes(
            issue_id, enroll, sname, sbranch, smobile, semail, comp_id, qty,
            issue_time_str, return_time_str, "ISSUED", self.current_user or "admin"
        )
        self.send_async_email(semail, subject, body, pdf_bytes, f"GatePass_Slip_{enroll}_{comp_id}.pdf")

    def clear_window(self):
        for widget in self.winfo_children():
            widget.destroy()

    def apply_table_styling(self):
        style = ttk.Style()
        style.theme_use("default")

        style.configure(
            "Treeview",
            background=THEME["card_bg"],
            foreground=THEME["text_main"],
            fieldbackground=THEME["card_bg"],
            rowheight=34,
            font=("Segoe UI", 10),
            borderwidth=0
        )
        style.map(
            "Treeview",
            background=[("selected", THEME["primary"])],
            foreground=[("selected", "#FFFFFF")]
        )

        style.configure(
            "Treeview.Heading",
            background=THEME["card_highlight"],
            foreground=THEME["text_main"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padding=(8, 8)
        )
        style.map(
            "Treeview.Heading",
            background=[("active", THEME["card_border"])]
        )

    def show_login_screen(self):
        self.clear_window()
        self.current_user = None
        self.current_role = None
        self.is_student_mode = False

        bg_canvas = FullAnimatedMeshCanvas(self, width=1400, height=820, bg=THEME["bg_main"])
        bg_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)

        header_bar = ctk.CTkFrame(self, height=110, fg_color="transparent")
        header_bar.place(relx=0, rely=0, relwidth=1)

        if self.su_logo:
            ctk.CTkLabel(header_bar, image=self.su_logo, text="").pack(side="left", padx=40, pady=10)
        else:
            ctk.CTkLabel(header_bar, text="SARVAJANIK UNIVERSITY", text_color=THEME["primary"], font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold")).pack(side="left", padx=40, pady=25)

        if self.scet_logo:
            ctk.CTkLabel(header_bar, image=self.scet_logo, text="").pack(side="right", padx=40, pady=10)
        else:
            ctk.CTkLabel(header_bar, text="SCET SURAT", text_color=THEME["accent_gold"], font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold")).pack(side="right", padx=40, pady=25)

        title_center = ctk.CTkFrame(header_bar, fg_color="transparent")
        title_center.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            title_center,
            text="SARVAJANIK COLLEGE OF ENGINEERING & TECHNOLOGY",
            text_color=THEME["primary"],
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
        ).pack()

        ctk.CTkLabel(
            title_center,
            text="Sarvajanik University, Surat • Laboratory Resource & Management System",
            text_color=THEME["text_muted"],
            font=ctk.CTkFont(family="Segoe UI", size=12)
        ).pack(pady=(2, 0))

        self.box_width = 880
        self.box_height = 500

        self.container = ctk.CTkFrame(
            self,
            width=self.box_width,
            height=self.box_height,
            corner_radius=22,
            fg_color=THEME["card_bg"],
            border_width=1,
            border_color=THEME["card_border"]
        )
        self.container.pack_propagate(False)
        self.container.place(relx=0.5, rely=0.54, anchor="center")

        self.left_panel = ctk.CTkFrame(self.container, fg_color="transparent")
        self.left_panel.pack_propagate(False)
        self.left_panel.place(relx=0.0, rely=0.0, relwidth=0.5, relheight=1.0)

        self.right_panel = ctk.CTkFrame(self.container, fg_color="transparent")
        self.right_panel.pack_propagate(False)
        self.right_panel.place(relx=0.5, rely=0.0, relwidth=0.5, relheight=1.0)

        self.build_student_form()
        self.build_admin_form()

        self.overlay = ctk.CTkFrame(self.container, corner_radius=22, fg_color=THEME["hero_gradient"], border_width=0)
        self.overlay.pack_propagate(False)
        self.overlay.place(relx=0.0, rely=0.0, relwidth=0.5, relheight=1.0)

        self.build_overlay_content()

    def build_overlay_content(self):
        for w in self.overlay.winfo_children():
            w.destroy()

        self.overlay_badge = ctk.CTkFrame(self.overlay, fg_color="#60A5FA", corner_radius=15, height=26)
        self.overlay_badge.pack(pady=(70, 10), padx=30)
        ctk.CTkLabel(self.overlay_badge, text="SCET ACADEMIC GATEWAY", text_color="#FFFFFF", font=ctk.CTkFont(size=10, weight="bold")).pack(padx=14, pady=3)

        self.overlay_title = ctk.CTkLabel(
            self.overlay,
            text="Hello, Welcome!",
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold")
        )
        self.overlay_title.pack(pady=(8, 8))

        self.overlay_subtitle = ctk.CTkLabel(
            self.overlay,
            text="Access the SCET Hardware Resource & Issue Tracking System.",
            text_color="#EFF6FF",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            wraplength=310,
            justify="center"
        )
        self.overlay_subtitle.pack(pady=(0, 45))

        self.overlay_prompt = ctk.CTkLabel(
            self.overlay,
            text="Student without Admin credentials?",
            text_color="#DBEAFE",
            font=ctk.CTkFont(family="Segoe UI", size=12)
        )
        self.overlay_prompt.pack(pady=(0, 10))

        self.overlay_btn = ctk.CTkButton(
            self.overlay,
            text="Switch to Student Catalog",
            command=self.toggle_flip_view,
            width=230,
            height=44,
            fg_color="transparent",
            border_width=2,
            border_color="#FFFFFF",
            hover_color=THEME["hero_gradient_dark"],
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            corner_radius=25
        )
        self.overlay_btn.pack()

    def build_admin_form(self):
        for w in self.right_panel.winfo_children():
            w.destroy()

        badge = ctk.CTkFrame(self.right_panel, fg_color="#EFF6FF", corner_radius=15, height=26)
        badge.pack(pady=(45, 6), padx=30)
        ctk.CTkLabel(badge, text="FACULTY / ADMIN PORTAL", text_color=THEME["primary"], font=ctk.CTkFont(size=10, weight="bold")).pack(padx=14, pady=3)

        ctk.CTkLabel(
            self.right_panel,
            text="Admin Login",
            text_color=THEME["text_main"],
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold")
        ).pack(pady=(4, 2))

        ctk.CTkLabel(
            self.right_panel,
            text="Enter authorized laboratory credentials",
            text_color=THEME["text_muted"],
            font=ctk.CTkFont(family="Segoe UI", size=12)
        ).pack(pady=(0, 25))

        self.login_user = ctk.CTkEntry(
            self.right_panel,
            placeholder_text="Username",
            width=320,
            height=44,
            fg_color=THEME["input_bg"],
            border_color=THEME["card_border"],
            text_color=THEME["text_main"],
            corner_radius=12
        )
        self.login_user.pack(pady=6)

        self.login_pass = ctk.CTkEntry(
            self.right_panel,
            placeholder_text="Password",
            show="*",
            width=320,
            height=44,
            fg_color=THEME["input_bg"],
            border_color=THEME["card_border"],
            text_color=THEME["text_main"],
            corner_radius=12
        )
        self.login_pass.pack(pady=6)

        admin_btn = ctk.CTkButton(
            self.right_panel,
            text="Login as Admin",
            command=self.handle_admin_login,
            width=320,
            height=46,
            fg_color=THEME["primary"],
            hover_color=THEME["primary_hover"],
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            corner_radius=12
        )
        admin_btn.pack(pady=(20, 10))

    def build_student_form(self):
        for w in self.left_panel.winfo_children():
            w.destroy()

        badge = ctk.CTkFrame(self.left_panel, fg_color="#FEF3C7", corner_radius=15, height=26)
        badge.pack(pady=(55, 6), padx=30)
        ctk.CTkLabel(badge, text="STUDENT CATALOG", text_color=THEME["accent_gold"], font=ctk.CTkFont(size=10, weight="bold")).pack(padx=14, pady=3)

        ctk.CTkLabel(
            self.left_panel,
            text="Student Browse Mode",
            text_color=THEME["text_main"],
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold")
        ).pack(pady=(4, 2))

        ctk.CTkLabel(
            self.left_panel,
            text="Explore lab hardware, inventory availability, and technical specifications without signing in.",
            text_color=THEME["text_muted"],
            font=ctk.CTkFont(family="Segoe UI", size=12),
            wraplength=310,
            justify="center"
        ).pack(pady=(0, 35))

        student_btn = ctk.CTkButton(
            self.left_panel,
            text="Continue to Inventory ➔",
            command=self.handle_student_portal_launch,
            width=320,
            height=48,
            fg_color=THEME["primary"],
            hover_color=THEME["primary_hover"],
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            corner_radius=12
        )
        student_btn.pack(pady=10)

    def toggle_flip_view(self):
        if self.animating_flip:
            return

        self.animating_flip = True
        target_relx = 0.5 if not self.is_student_mode else 0.0
        current_relx = 0.0 if not self.is_student_mode else 0.5
        step = 0.04 if target_relx > current_relx else -0.04

        def animate_slide():
            nonlocal current_relx
            if (step > 0 and current_relx < target_relx) or (step < 0 and current_relx > target_relx):
                current_relx += step
                if (step > 0 and current_relx > target_relx) or (step < 0 and current_relx < target_relx):
                    current_relx = target_relx
                self.overlay.place(relx=current_relx, rely=0.0, relwidth=0.5, relheight=1.0)
                self.after(14, animate_slide)
            else:
                self.overlay.place(relx=target_relx, rely=0.0, relwidth=0.5, relheight=1.0)
                self.is_student_mode = not self.is_student_mode
                self.animating_flip = False

                if self.is_student_mode:
                    self.overlay_title.configure(text="Welcome Back!")
                    self.overlay_subtitle.configure(text="Already have authorized faculty or laboratory administrator credentials?")
                    self.overlay_prompt.configure(text="Are you a Faculty Administrator?")
                    self.overlay_btn.configure(text="Switch to Admin Login")
                else:
                    self.overlay_title.configure(text="Hello, Welcome!")
                    self.overlay_subtitle.configure(text="Access the SCET Hardware Resource & Issue Tracking System.")
                    self.overlay_prompt.configure(text="Student without Admin credentials?")
                    self.overlay_btn.configure(text="Switch to Student Catalog")

        animate_slide()

    def _login_rate_limited(self, username):
        """In-process lockout: max 5 attempts per username per 60s window.
        Resets on a successful login."""
        now = time.time()
        window_seconds = 60
        max_attempts = 5
        attempts = self._login_attempts.setdefault(username, [])
        attempts[:] = [t for t in attempts if now - t < window_seconds]
        if len(attempts) >= max_attempts:
            return True
        attempts.append(now)
        return False

    def handle_admin_login(self):
        # Machine-identity check: this process must be running on the
        # server whose own network interface is in SECURITY_CONFIG's
        # allowed_subnets (i.e. the 10.194.87.212 host itself). This is a
        # local, client-side check — it establishes "this copy of the app
        # is running on the trusted machine," not "this network request
        # came from a trusted machine." Keep physical/Windows-account
        # access to that machine restricted; this check is a second layer,
        # not a substitute for that.
        if not self.verify_scet_network():
            return

        username = self.login_user.get().strip()
        password = self.login_pass.get().strip()

        if not username or not password:
            messagebox.showerror("Error", "Please enter both username and password.")
            return

        if self._login_rate_limited(username):
            messagebox.showerror(
                "Too Many Attempts",
                "Too many failed login attempts for this account. Please wait a minute and try again."
            )
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT role, password_hash FROM system_users WHERE username = :1 AND is_active = 1",
                (username,)
            )
            row = cursor.fetchone()

            if not row:
                cursor.close()
                conn.close()
                messagebox.showerror("Access Denied", "Invalid admin credentials or account is inactive.")
                return

            role, stored_hash = row

            if role.upper() != "ADMIN" or not auth_utils.verify_password(password, stored_hash):
                cursor.close()
                conn.close()
                messagebox.showerror("Access Denied", "Invalid admin credentials or account is inactive.")
                return

            # Lazy migration: the first successful login against a legacy
            # plaintext row (or an Argon2 hash with outdated parameters)
            # immediately re-hashes it with current Argon2id settings.
            # A failure here never blocks the login that just succeeded.
            if auth_utils.needs_rehash(stored_hash):
                try:
                    new_hash = auth_utils.hash_password(password)
                    cursor.execute(
                        "UPDATE system_users SET password_hash = :1 WHERE username = :2",
                        (new_hash, username)
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()

            cursor.close()
            conn.close()

            self._login_attempts.pop(username, None)
            self.current_user = username
            self.show_branch_selection_screen()
        except Exception as e:
            messagebox.showerror("Database Connection Error", f"Could not connect to Oracle:\n{e}")

    def handle_student_portal_launch(self):
        branches = self.fetch_branches(remote=True)
        if branches is None:
            messagebox.showerror(
                "Connection Error",
                "Could not reach the lab server right now.\n\nCheck your network connection and try again."
            )
            return
        if not branches:
            messagebox.showinfo("No Departments", "No branches or lab inventories have been registered yet by faculty.")
            return
        self.selected_branch.set(branches[0])
        self.launch_main_portal("student")

    def show_branch_selection_screen(self):
        self.clear_window()

        bg_canvas = FullAnimatedMeshCanvas(self, width=1400, height=820, bg=THEME["bg_main"])
        bg_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)

        header_bar = ctk.CTkFrame(self, height=110, fg_color="transparent")
        header_bar.place(relx=0, rely=0, relwidth=1)

        if self.su_logo:
            ctk.CTkLabel(header_bar, image=self.su_logo, text="").pack(side="left", padx=40, pady=10)
        else:
            ctk.CTkLabel(header_bar, text="SARVAJANIK UNIVERSITY", text_color=THEME["primary"], font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold")).pack(side="left", padx=40, pady=25)

        if self.scet_logo:
            ctk.CTkLabel(header_bar, image=self.scet_logo, text="").pack(side="right", padx=40, pady=10)
        else:
            ctk.CTkLabel(header_bar, text="SCET SURAT", text_color=THEME["accent_gold"], font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold")).pack(side="right", padx=40, pady=25)

        title_center = ctk.CTkFrame(header_bar, fg_color="transparent")
        title_center.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            title_center,
            text="SARVAJANIK COLLEGE OF ENGINEERING & TECHNOLOGY",
            text_color=THEME["primary"],
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
        ).pack()

        ctk.CTkLabel(
            title_center,
            text="Sarvajanik University, Surat • Laboratory Resource & Management System",
            text_color=THEME["text_muted"],
            font=ctk.CTkFont(family="Segoe UI", size=12)
        ).pack(pady=(2, 0))

        card = ctk.CTkFrame(
            self,
            width=500,
            height=620,
            corner_radius=22,
            fg_color=THEME["card_bg"],
            border_width=1,
            border_color=THEME["card_border"]
        )
        card.pack_propagate(False)
        card.place(relx=0.5, rely=0.54, anchor="center")

        badge = ctk.CTkFrame(card, fg_color="#EFF6FF", corner_radius=15, height=26)
        badge.pack(pady=(20, 4), padx=30)
        ctk.CTkLabel(badge, text="DEPARTMENT ACCESS GATEWAY", text_color=THEME["primary"], font=ctk.CTkFont(size=10, weight="bold")).pack(padx=14, pady=3)

        ctk.CTkLabel(
            card,
            text="Select Department / Branch",
            text_color=THEME["text_main"],
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold")
        ).pack(pady=(4, 2))

        ctk.CTkLabel(
            card,
            text="Each department is isolated and password-protected",
            text_color=THEME["text_muted"],
            font=ctk.CTkFont(family="Segoe UI", size=12)
        ).pack(pady=(0, 12))

        branches = self.fetch_branches()

        if branches:
            if not self.selected_branch.get() or self.selected_branch.get() not in branches:
                self.selected_branch.set(branches[0])

            ctk.CTkLabel(card, text="Registered Branch:", font=ctk.CTkFont(size=12, weight="bold"), text_color=THEME["text_muted"]).pack(anchor="w", padx=50, pady=(2, 2))
            self.branch_select_menu = ctk.CTkOptionMenu(
                card,
                values=branches,
                variable=self.selected_branch,
                width=400,
                height=38,
                fg_color=THEME["input_bg"],
                text_color=THEME["text_main"],
                button_color=THEME["primary"],
                button_hover_color=THEME["primary_hover"],
                corner_radius=10
            )
            self.branch_select_menu.pack(pady=2)

            ctk.CTkLabel(card, text="Branch Security Password:", font=ctk.CTkFont(size=12, weight="bold"), text_color=THEME["text_muted"]).pack(anchor="w", padx=50, pady=(4, 2))
            self.branch_pass_entry = ctk.CTkEntry(
                card,
                placeholder_text="Enter Branch Password",
                show="*",
                width=400,
                height=38,
                fg_color=THEME["input_bg"],
                border_color=THEME["card_border"],
                text_color=THEME["text_main"],
                corner_radius=10
            )
            self.branch_pass_entry.pack(pady=2)

            ctk.CTkButton(
                card,
                text="Enter Department Portal ➔",
                width=400,
                height=40,
                fg_color=THEME["primary"],
                hover_color=THEME["primary_hover"],
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                corner_radius=10,
                command=self.authenticate_branch_entry
            ).pack(pady=(10, 2))

            ctk.CTkButton(
                card,
                text="Forgot Branch Password? (Reset via Email OTP)",
                width=400,
                height=28,
                fg_color="transparent",
                hover_color=THEME["card_highlight"],
                text_color=THEME["primary"],
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                command=self.open_forgot_password_modal
            ).pack(pady=1)

            ctk.CTkButton(
                card,
                text="🗑️ Delete Branch (Requires Password & Email OTP)",
                width=400,
                height=32,
                fg_color="transparent",
                hover_color="#FEE2E2",
                text_color=THEME["accent_red"],
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                corner_radius=8,
                command=self.open_delete_branch_modal
            ).pack(pady=2)
        else:
            ctk.CTkLabel(
                card,
                text="No branches registered yet.\nPlease create your department below.",
                text_color=THEME["accent_gold"],
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                wraplength=380,
                justify="center"
            ).pack(pady=25)

        ctk.CTkLabel(card, text="━━━━━  OR  ━━━━━", text_color=THEME["card_border"], font=ctk.CTkFont(size=10, weight="bold")).pack(pady=2)

        ctk.CTkButton(
            card,
            text="+ Register Branch",
            width=400,
            height=36,
            fg_color=THEME["accent_green"],
            hover_color=THEME["accent_green_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=10,
            command=self.open_create_branch_modal
        ).pack(pady=2)

        ctk.CTkButton(
            card,
            text="⬅ Back to Login",
            width=400,
            height=32,
            fg_color="transparent",
            hover_color=THEME["card_highlight"],
            text_color=THEME["text_muted"],
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            corner_radius=10,
            command=self.show_login_screen
        ).pack(pady=(2, 5))

    def open_create_branch_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Register New Department Branch")
        modal.geometry("480x480")
        modal.grab_set()

        ctk.CTkLabel(modal, text="Register Department Branch", font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"), text_color=THEME["primary"]).pack(pady=(20, 5))
        ctk.CTkLabel(modal, text="Set up branch authentication and recovery details", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=THEME["text_muted"]).pack(pady=(0, 15))

        code_entry = self.create_styled_entry(modal, "Branch Code (e.g. CO, IT, EC, AI, ME)")
        name_entry = self.create_styled_entry(modal, "Full Branch Name (e.g. Electronics & Comm.)")
        pass_entry = self.create_styled_entry(modal, "Branch Password (to isolate this department)")
        pass_entry.configure(show="*")
        email_entry = self.create_styled_entry(modal, "Faculty Recovery Email (for OTP Reset)")

        def save_branch():
            code = code_entry.get().strip().upper()
            bname = name_entry.get().strip() or code
            bpass = pass_entry.get().strip()
            remail = email_entry.get().strip()

            if not code or not bpass or not remail:
                messagebox.showerror("Validation Error", "Branch Code, Password, and Recovery Email are strictly required.", parent=modal)
                return

            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT INTO academic_branches (branch_code, branch_name, branch_password, recovery_email)
                    VALUES (:1, :2, :3, :4)
                """, (code, bname, bpass, remail))

                conn.commit()
                cursor.close()
                conn.close()

                messagebox.showinfo("Success", f"Department '{code}' registered successfully with password protection.", parent=modal)
                modal.destroy()
                self.selected_branch.set(code)
                self.show_branch_selection_screen()
            except Exception as e:
                messagebox.showerror("Error", f"Could not create branch:\n{e}", parent=modal)

        ctk.CTkButton(
            modal,
            text="Confirm & Register Branch",
            fg_color=THEME["accent_green"],
            hover_color=THEME["accent_green_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            height=42,
            corner_radius=10,
            command=save_branch
        ).pack(fill="x", padx=15, pady=(20, 10))

    def open_forgot_password_modal(self):
        branch = self.selected_branch.get()
        if not branch:
            messagebox.showwarning("Warning", "Please select a branch first.")
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT recovery_email FROM academic_branches WHERE branch_code = :1", (branch,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()

            if not row or not row[0]:
                messagebox.showerror("Error", f"No recovery email registered for Branch '{branch}'.")
                return

            recovery_email = row[0]
        except Exception as e:
            messagebox.showerror("Database Error", str(e))
            return

        modal = ctk.CTkToplevel(self)
        modal.title(f"Password Reset - Branch {branch}")
        modal.geometry("480x520")
        modal.grab_set()

        ctk.CTkLabel(modal, text=f"Reset Password for {branch}", font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"), text_color=THEME["primary"]).pack(pady=(20, 4))
        
        masked_email = recovery_email[:3] + "****" + recovery_email[recovery_email.find("@"):] if "@" in recovery_email else recovery_email
        ctk.CTkLabel(modal, text=f"A verification code will be sent to registered email:\n{masked_email}", font=ctk.CTkFont(family="Segoe UI", size=12), text_color=THEME["text_muted"], justify="center").pack(pady=(0, 10))

        send_status_lbl = ctk.CTkLabel(modal, text="", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["accent_green"])
        send_status_lbl.pack(pady=2)

        current_otp = [None]

        def handle_send_otp():
            generated = f"{random.randint(100000, 999999)}"
            current_otp[0] = generated

            success, msg = self.send_otp_email(recovery_email, generated, branch)
            if success:
                send_status_lbl.configure(text=f"✓ OTP successfully sent to {masked_email}", text_color=THEME["accent_green"])
            else:
                send_status_lbl.configure(text=f"✗ Email failed: {msg}", text_color=THEME["accent_red"])
                messagebox.showerror("Delivery Error", f"Failed to send email:\n{msg}\n\nPlease verify SMTP configuration in config.json.", parent=modal)

        ctk.CTkButton(
            modal,
            text="Send Verification OTP to Email",
            fg_color=THEME["primary"],
            hover_color=THEME["primary_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            height=36,
            corner_radius=8,
            command=handle_send_otp
        ).pack(fill="x", padx=25, pady=(4, 15))

        ctk.CTkLabel(modal, text="Enter 6-Digit OTP & Set New Password", font=ctk.CTkFont(size=12, weight="bold"), text_color=THEME["text_main"]).pack(anchor="w", padx=25, pady=(5, 2))

        entry_otp = self.create_styled_entry(modal, "6-Digit OTP Code")
        entry_new_pass = self.create_styled_entry(modal, "New Branch Password")
        entry_new_pass.configure(show="*")
        entry_confirm_pass = self.create_styled_entry(modal, "Confirm New Password")
        entry_confirm_pass.configure(show="*")

        def handle_confirm_password_reset():
            otp_val = entry_otp.get().strip()
            p1 = entry_new_pass.get().strip()
            p2 = entry_confirm_pass.get().strip()

            if not current_otp[0]:
                messagebox.showwarning("Warning", "Please click 'Send Verification OTP to Email' first.", parent=modal)
                return

            if otp_val != current_otp[0]:
                messagebox.showerror("Error", "Invalid OTP entered. Please check your email inbox.", parent=modal)
                return

            if not p1 or not p2:
                messagebox.showerror("Error", "Please enter and confirm your new password.", parent=modal)
                return

            if p1 != p2:
                messagebox.showerror("Error", "Passwords do not match. Please re-enter.", parent=modal)
                return

            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE academic_branches SET branch_password = :1 WHERE branch_code = :2", (p1, branch))
                conn.commit()
                cursor.close()
                conn.close()

                messagebox.showinfo("Success", f"Password for Department '{branch}' reset successfully.", parent=modal)
                modal.destroy()
                if hasattr(self, "branch_pass_entry"):
                    self.branch_pass_entry.delete(0, "end")
            except Exception as e:
                messagebox.showerror("Database Error", str(e), parent=modal)

        ctk.CTkButton(
            modal,
            text="Confirm & Update Password",
            fg_color=THEME["accent_green"],
            hover_color=THEME["accent_green_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            height=42,
            corner_radius=10,
            command=handle_confirm_password_reset
        ).pack(fill="x", padx=25, pady=(15, 10))

    def open_delete_branch_modal(self):
        branch = self.selected_branch.get()
        if not branch:
            messagebox.showwarning("Warning", "Please select a branch first.")
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT branch_password, recovery_email FROM academic_branches WHERE branch_code = :1", (branch,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()

            if not row:
                messagebox.showerror("Error", f"Branch '{branch}' not found.")
                return

            correct_pass = row[0]
            recovery_email = row[1]
        except Exception as e:
            messagebox.showerror("Database Error", str(e))
            return

        modal = ctk.CTkToplevel(self)
        modal.title(f"Delete Branch - {branch}")
        modal.geometry("480x560")
        modal.grab_set()

        ctk.CTkLabel(modal, text=f"Delete Branch '{branch}'", font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"), text_color=THEME["accent_red"]).pack(pady=(20, 4))
        ctk.CTkLabel(modal, text="This will permanently delete the branch, all its inventory,\nand issue history. Verification required.", font=ctk.CTkFont(family="Segoe UI", size=11), text_color=THEME["text_muted"], justify="center").pack(pady=(0, 10))

        ctk.CTkLabel(modal, text="Enter Branch Password:", font=ctk.CTkFont(size=12, weight="bold"), text_color=THEME["text_main"]).pack(anchor="w", padx=25, pady=(5, 2))
        pass_entry = self.create_styled_entry(modal, "Branch Password")
        pass_entry.configure(show="*")

        masked_email = recovery_email[:3] + "****" + recovery_email[recovery_email.find("@"):] if recovery_email and "@" in recovery_email else (recovery_email or "N/A")
        ctk.CTkLabel(modal, text=f"Recovery Email: {masked_email}", font=ctk.CTkFont(size=11), text_color=THEME["text_muted"]).pack(anchor="w", padx=25, pady=(10, 2))

        send_status_lbl = ctk.CTkLabel(modal, text="", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["accent_green"])
        send_status_lbl.pack(pady=2)

        current_otp = [None]

        def handle_send_otp():
            entered_pass = pass_entry.get().strip()
            if correct_pass and entered_pass != correct_pass:
                messagebox.showerror("Error", "Incorrect branch password entered.", parent=modal)
                return

            if not recovery_email:
                messagebox.showerror("Error", "No recovery email registered for this branch.", parent=modal)
                return

            generated = f"{random.randint(100000, 999999)}"
            current_otp[0] = generated

            success, msg = self.send_otp_email(recovery_email, generated, branch)
            if success:
                send_status_lbl.configure(text=f"✓ OTP successfully sent to {masked_email}", text_color=THEME["accent_green"])
            else:
                send_status_lbl.configure(text=f"✗ Email failed: {msg}", text_color=THEME["accent_red"])
                messagebox.showerror("Delivery Error", f"Failed to send email:\n{msg}", parent=modal)

        ctk.CTkButton(
            modal,
            text="Verify Password & Send OTP",
            fg_color=THEME["primary"],
            hover_color=THEME["primary_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            height=36,
            corner_radius=8,
            command=handle_send_otp
        ).pack(fill="x", padx=25, pady=(4, 10))

        ctk.CTkLabel(modal, text="Enter 6-Digit OTP:", font=ctk.CTkFont(size=12, weight="bold"), text_color=THEME["text_main"]).pack(anchor="w", padx=25, pady=(5, 2))
        entry_otp = self.create_styled_entry(modal, "6-Digit OTP Code")

        def handle_confirm_deletion():
            entered_pass = pass_entry.get().strip()
            otp_val = entry_otp.get().strip()

            if correct_pass and entered_pass != correct_pass:
                messagebox.showerror("Error", "Incorrect branch password entered.", parent=modal)
                return

            if not current_otp[0]:
                messagebox.showwarning("Warning", "Please request and receive the OTP first.", parent=modal)
                return

            if otp_val != current_otp[0]:
                messagebox.showerror("Error", "Invalid OTP entered.", parent=modal)
                return

            if not messagebox.askyesno("Final Confirmation", f"Are you absolutely sure you want to permanently delete Branch '{branch}' and all associated data?", parent=modal):
                return

            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM component_images WHERE branch_code = :1", (branch,))
                cursor.execute("DELETE FROM issue_records WHERE branch_code = :1", (branch,))
                cursor.execute("DELETE FROM lab_components WHERE branch_code = :1", (branch,))
                cursor.execute("DELETE FROM academic_branches WHERE branch_code = :1", (branch,))
                conn.commit()
                cursor.close()
                conn.close()

                self.reindex_issue_ids()

                messagebox.showinfo("Deleted", f"Branch '{branch}' has been deleted successfully.", parent=modal)
                modal.destroy()

                branches = self.fetch_branches()
                if branches:
                    self.selected_branch.set(branches[0])
                else:
                    self.selected_branch.set("")
                self.show_branch_selection_screen()
            except Exception as e:
                messagebox.showerror("Database Error", str(e), parent=modal)

        ctk.CTkButton(
            modal,
            text="Permanently Delete Branch",
            fg_color=THEME["accent_red"],
            hover_color=THEME["accent_red_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            height=42,
            corner_radius=10,
            command=handle_confirm_deletion
        ).pack(fill="x", padx=25, pady=(15, 10))

    def authenticate_branch_entry(self):
        branch = self.selected_branch.get()
        entered_pass = self.branch_pass_entry.get().strip()

        if not branch:
            messagebox.showwarning("Warning", "Select a branch.")
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT branch_password FROM academic_branches WHERE branch_code = :1", (branch,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()

            if not row or not row[0]:
                self.selected_branch.set(branch)
                self.launch_main_portal("admin")
                return

            correct_pass = row[0]
            if entered_pass == correct_pass:
                self.selected_branch.set(branch)
                self.launch_main_portal("admin")
            else:
                messagebox.showerror("Access Denied", f"Incorrect password for Department Branch '{branch}'.")
        except Exception as e:
            messagebox.showerror("Database Error", str(e))

    def verify_branch_admin_password(self, branch_code, action_desc="perform this operation"):
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT branch_password FROM academic_branches WHERE branch_code = :1", (branch_code,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()

            if not row or not row[0]:
                return True

            correct_pass = row[0]
            entered = simpledialog.askstring("Security Authorization", f"Enter password for Branch '{branch_code}' to {action_desc}:", show="*", parent=self)
            if not entered:
                return False

            if entered.strip() == correct_pass:
                return True
            else:
                messagebox.showerror("Access Denied", f"Incorrect password for Branch '{branch_code}'.")
                return False
        except Exception as e:
            messagebox.showerror("Database Error", str(e))
            return False

    def fetch_branches(self, remote=False):
        if remote:
            try:
                resp = requests.get(f"{STUDENT_API_BASE}/api/branches", timeout=STUDENT_API_TIMEOUT)
                resp.raise_for_status()
                return [b["branch_code"] for b in resp.json()]
            except Exception:
                return None  # None = couldn't reach the server; [] = server reached, genuinely no branches yet

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT branch_code FROM academic_branches ORDER BY branch_code")
            branches = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            return branches
        except Exception:
            return None  # None = couldn't reach the server; [] = server reached, genuinely no branches yet

    def refresh_all_views(self):
        self.populate_inventory_table()
        if hasattr(self, "issue_table"):
            self.refresh_issue_dropdown()
            self.populate_issue_table()
        self.refresh_student_history_table()
        self.update_live_stats()

    def update_live_stats(self):
        if not hasattr(self, "stat_items_lbl"):
            return

        b_code = self.selected_branch.get()
        if not b_code:
            return

        if self.current_role == "student":
            try:
                resp = requests.get(f"{STUDENT_API_BASE}/api/branches/{b_code}/stats", timeout=STUDENT_API_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                self.stat_items_lbl.configure(text=f"Total Items: {data['total_items']}")
                self.stat_stock_lbl.configure(text=f"Stock Units: {data['total_qty']}")
                self.stat_issued_lbl.configure(text=f"Active Issued: {data['total_issued']}")
                self.stat_overdue_lbl.configure(text=f"Overdue: {data['total_overdue']}")
            except Exception:
                pass
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*), NVL(SUM(total_qty), 0), NVL(SUM(issued_qty), 0)
                FROM lab_components
                WHERE branch_code = :1
            """, (b_code,))
            tot_items, tot_qty, tot_issued = cursor.fetchone()

            cursor.execute("""
                SELECT COUNT(*) FROM issue_records
                WHERE branch_code = :1 AND status = 'ISSUED' AND due_date < SYSDATE
            """, (b_code,))
            tot_overdue = cursor.fetchone()[0]

            cursor.close()
            conn.close()

            self.stat_items_lbl.configure(text=f"Total Items: {tot_items}")
            self.stat_stock_lbl.configure(text=f"Stock Units: {tot_qty}")
            self.stat_issued_lbl.configure(text=f"Active Issued: {tot_issued}")
            self.stat_overdue_lbl.configure(text=f"Overdue: {tot_overdue}")
        except Exception:
            pass

    def open_add_branch_dialog(self):
        self.open_create_branch_modal()

    def remove_branch_action(self):
        self.open_delete_branch_modal()

    def export_inventory_pdf(self):
        b_code = self.selected_branch.get()
        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF Document", "*.pdf")],
            initialfile=f"SCET_FullStock_{b_code}.pdf"
        )
        if not file_path:
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT comp_id, comp_name, category, specifications, total_qty, issued_qty, available_qty
                FROM lab_components
                WHERE branch_code = :1
                ORDER BY comp_id ASC
            """, (b_code,))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            doc = SimpleDocTemplate(file_path, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
            styles = getSampleStyleSheet()
            elements = []

            elements.extend(self.build_pdf_header(
                "SARVAJANIK COLLEGE OF ENGINEERING & TECHNOLOGY (SCET)",
                f"DEPARTMENT HARDWARE FULL STOCK INVENTORY REPORT ({b_code})"
            ))

            meta_text = f"<b>Department:</b> {b_code} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Report Date:</b> {datetime.now().strftime('%d-%b-%Y %I:%M:%S %p')}"
            elements.append(Paragraph(meta_text, ParagraphStyle("Meta", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#64748B"), spaceAfter=10)))

            table_data = [["CODE", "NAME", "CATEGORY", "SPECS", "TOT", "ISS", "AVL"]]
            for r in rows:
                table_data.append([str(r[0]), str(r[1]), str(r[2]), str(r[3] or '-'), str(r[4]), str(r[5]), str(r[6])])

            t = Table(table_data, colWidths=[80, 160, 100, 110, 45, 45, 45])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2563EB")),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8.5),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('ALIGN', (1,1), (1,-1), 'LEFT'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
                ('PADDING', (0,0), (-1,-1), 5),
            ]))
            elements.append(t)

            doc.build(elements)
            messagebox.showinfo("Export Successful", f"Full stock inventory PDF saved to:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Could not generate stock PDF:\n{e}")

    def generate_procurement_slip(self):
        threshold_input = simpledialog.askstring(
            "Low Stock Threshold",
            "Enter threshold count to consider as low quantity (e.g. 3, 5, 10):",
            parent=self,
            initialvalue="3"
        )
        if threshold_input is None:
            return

        try:
            threshold_val = int(threshold_input.strip())
            if threshold_val < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter a valid positive whole number.")
            return

        b_code = self.selected_branch.get()
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT comp_id, comp_name, category, available_qty, total_qty
                FROM lab_components
                WHERE branch_code = :1 AND available_qty <= :2
                ORDER BY available_qty ASC
            """, (b_code, threshold_val))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if not rows:
                messagebox.showinfo("Stock Healthy", f"All components in [{b_code}] have available stock > {threshold_val} units.")
                return

            file_path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF Document", "*.pdf")],
                initialfile=f"SCET_Procurement_Slip_{b_code}.pdf"
            )
            if not file_path:
                return

            doc = SimpleDocTemplate(file_path, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
            styles = getSampleStyleSheet()
            elements = []

            elements.extend(self.build_pdf_header(
                "SARVAJANIK COLLEGE OF ENGINEERING & TECHNOLOGY (SCET)",
                f"DEPARTMENT HARDWARE REORDER & PROCUREMENT REQUEST ({b_code})"
            ))

            meta_text = f"<b>Criterion:</b> Stock ≤ {threshold_val} units &nbsp;&nbsp;|&nbsp;&nbsp; <b>Generated:</b> {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
            elements.append(Paragraph(meta_text, ParagraphStyle("Meta", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#64748B"), spaceAfter=10)))

            table_data = [["ITEM CODE", "COMPONENT NAME", "CATEGORY", "AVAIL", "TOTAL"]]
            for r in rows:
                table_data.append([str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])])

            t = Table(table_data, colWidths=[100, 200, 130, 60, 60])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#D97706")),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8.5),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('ALIGN', (1,1), (1,-1), 'LEFT'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
                ('PADDING', (0,0), (-1,-1), 5),
            ]))
            elements.append(t)

            doc.build(elements)
            messagebox.showinfo("Slip Generated", f"Procurement PDF saved to:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate PDF:\n{e}")

    def get_active_issue_table(self):
        if hasattr(self, "history_table") and hasattr(self, "current_history_tab") and self.current_history_tab:
            try:
                if self.tabview.get() == self.current_history_tab:
                    return self.history_table
            except Exception:
                pass
        return getattr(self, "issue_table", None)

    def generate_gate_pass_receipt(self):
        target_table = self.get_active_issue_table()
        if not target_table:
            return

        selected = target_table.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select an active issue record from the table to print Gate-Pass.")
            return

        r = target_table.item(selected[0])["values"]
        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF Document", "*.pdf")],
            initialfile=f"GatePass_Slip_{r[1]}_{r[5]}.pdf"
        )
        if not file_path:
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT TO_CHAR(due_date, 'YYYY-MM-DD HH:MI:SS AM') FROM issue_records WHERE issue_id = :1", (r[0],))
            drow = cursor.fetchone()
            due_str = drow[0] if drow and drow[0] else "N/A"
            cursor.close()
            conn.close()

            student_email_val = r[15] if len(r) > 15 else ""
            pdf_bytes = self.generate_gate_pass_pdf_bytes(
                r[0], r[1], r[2], r[3], r[4], student_email_val, r[5], r[8], r[6], due_str, r[9], r[12]
            )
            with open(file_path, "wb") as f:
                f.write(pdf_bytes)

            messagebox.showinfo("Gate Pass Ready", f"Gate pass PDF generated successfully:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate PDF:\n{e}")

    def export_oracle_database_backup(self):
        b_code = self.selected_branch.get()
        file_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Backup File", "*.json")],
            initialfile=f"SCET_Backup_{b_code}_{datetime.now().strftime('%Y%m%d_%I%M%S_%p')}.json"
        )
        if not file_path:
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM academic_branches WHERE branch_code = :1", (b_code,))
            branches_data = [list(r) for r in cursor.fetchall()]

            cursor.execute("""
                SELECT comp_id, comp_name, category, specifications, branch_code, total_qty, issued_qty, available_qty 
                FROM lab_components WHERE branch_code = :1
            """, (b_code,))
            components_data = [list(r) for r in cursor.fetchall()]

            cursor.execute("SELECT * FROM issue_records WHERE branch_code = :1", (b_code,))
            issues_data = []
            for r in cursor.fetchall():
                row_list = []
                for val in r:
                    if isinstance(val, (datetime, oracledb.Timestamp)):
                        row_list.append(val.strftime("%Y-%m-%d %I:%M:%S %p"))
                    else:
                        row_list.append(val)
                issues_data.append(row_list)

            cursor.close()
            conn.close()

            backup_payload = {
                "backup_generated": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
                "branch_code": b_code,
                "academic_branches": branches_data,
                "lab_components": components_data,
                "issue_records": issues_data
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(backup_payload, f, indent=2)

            messagebox.showinfo("Backup Completed", f"Department database snapshot exported successfully:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Backup Failed", str(e))

    def simulate_fast_forward_overdue_record(self):
        target_table = self.get_active_issue_table()
        if not target_table:
            return

        selected = target_table.selection()
        if not selected:
            messagebox.showwarning("Testing Mode", "Select an active checkout row from the table to simulate overdue days.")
            return

        row = target_table.item(selected[0])["values"]
        issue_id = row[0]
        status = str(row[9]).upper()

        if "RETURNED" in status:
            messagebox.showinfo("Testing Mode", "This item is already returned. Select an active 'ISSUED' row.")
            return

        days_back = simpledialog.askinteger("Simulate Time Shift", "Enter number of overdue days to simulate (e.g. 3, 5, 7):", parent=self, initialvalue=3)
        if not days_back or days_back <= 0:
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE issue_records
                SET issue_date = SYSDATE - (:1 + 2),
                    due_date = SYSDATE - :1
                WHERE issue_id = :2
            """, (days_back, issue_id))
            conn.commit()
            cursor.close()
            conn.close()

            self.populate_issue_table()
            self.refresh_student_history_table()
            messagebox.showinfo("Simulation Success", f"Record #{issue_id} has been fast-forwarded by {days_back} day(s).\nIt is now marked as OVERDUE (+{days_back}d).")
        except Exception as e:
            messagebox.showerror("Simulation Error", str(e))

    def batch_email_overdue_reminders(self):
        if not SMTP_CONFIG.get("enabled", False):
            messagebox.showwarning("SMTP Disabled", "Please enable and configure SMTP in config.json to dispatch reminders.")
            return

        b_code = self.selected_branch.get()
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT issue_id, enrollment_no, student_name, comp_id, issue_qty, 
                       TO_CHAR(due_date, 'YYYY-MM-DD HH:MI:SS AM'), TRUNC(SYSDATE - due_date), student_branch, student_email
                FROM issue_records
                WHERE branch_code = :1 AND status = 'ISSUED' AND due_date < SYSDATE
            """, (b_code,))
            overdue_records = cursor.fetchall()
            cursor.close()
            conn.close()

            if not overdue_records:
                messagebox.showinfo("No Overdue Items", f"All issued hardware items in [{b_code}] are returned on time!")
                return

            if not messagebox.askyesno("Confirm Batch Alert", f"Dispatch warning reminder emails for {len(overdue_records)} overdue record(s)?"):
                return

            sent_count = 0
            for rec in overdue_records:
                enroll, sname, cid, qty, ddate, days_late, sbranch = rec[1], rec[2], rec[3], rec[4], rec[5], int(rec[6]), rec[7]
                student_email = rec[8] or f"{enroll.lower()}@scet.ac.in"
                penalty = days_late * 10

                subject = f"URGENT: Overdue Lab Hardware Return Notice - SCET {b_code}"
                body = (
                    f"Dear {sname},\n\n"
                    f"Greetings from Sarvajanik College of Engineering & Technology (SCET)!\n\n"
                    f"This is an automated reminder regarding an OVERDUE hardware component checkout:\n\n"
                    f"  • Student Name     : {sname}\n"
                    f"  • Enrollment No    : {enroll}\n"
                    f"  • Academic Branch  : {sbranch}\n"
                    f"  • Component Issued : {cid} (Quantity: {qty} Unit(s))\n"
                    f"  • Scheduled Return : {ddate}\n"
                    f"  • Days Overdue     : {days_late} Day(s)\n"
                    f"  • Current Penalty  : ₹{penalty} (Accruing at ₹10/day)\n\n"
                    f"Please return this hardware to Department {b_code} immediately to avoid examination hold and semester clearance suspension.\n\n"
                    f"Best Regards,\n"
                    f"Laboratory In-Charge & Faculty Staff\n"
                    f"Sarvajanik College of Engineering & Technology (SCET), Surat"
                )
                self.send_async_email(student_email, subject, body)
                sent_count += 1

            messagebox.showinfo("Reminders Sent", f"Dispatched {sent_count} official overdue email notice(s) in background.")
        except Exception as e:
            messagebox.showerror("Database Error", str(e))

    def generate_student_no_dues_certificate(self, default_enroll=""):
        enroll = default_enroll or simpledialog.askstring("No Dues Clearance", "Enter Student Enrollment Number for Clearance Check:", parent=self)
        if not enroll:
            return
        enroll = enroll.strip().upper()
        b_code = self.selected_branch.get()

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT comp_id, issue_qty, TO_CHAR(issue_date, 'YYYY-MM-DD HH:MI:SS AM')
                FROM issue_records
                WHERE UPPER(enrollment_no) = :1 AND status = 'ISSUED' AND branch_code = :2
            """, (enroll, b_code))
            pending_rows = cursor.fetchall()

            cursor.execute("SELECT student_name, student_branch FROM issue_records WHERE UPPER(enrollment_no) = :1", (enroll,))
            name_row = cursor.fetchone()
            student_name = name_row[0] if name_row else "Enrolled Student"
            student_branch = name_row[1] if name_row else b_code

            cursor.close()
            conn.close()

            if pending_rows:
                pending_details = "\n".join([f"  • {p[0]} (Qty: {p[1]}) (Issued: {p[2]})" for p in pending_rows])
                messagebox.showerror(
                    "Clearance Rejected - Dues Pending",
                    f"Student {enroll} ({student_name}) has {len(pending_rows)} unreturned lab component(s):\n\n{pending_details}\n\nClearance certificate cannot be issued until all items are returned."
                )
                return

            file_path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF Document", "*.pdf")],
                initialfile=f"NoDues_Certificate_{enroll}_{b_code}.pdf"
            )
            if not file_path:
                return

            doc = SimpleDocTemplate(file_path, pagesize=letter, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
            styles = getSampleStyleSheet()
            elements = []

            elements.extend(self.build_pdf_header(
                "SARVAJANIK COLLEGE OF ENGINEERING & TECHNOLOGY (SCET)",
                f"DEPARTMENT OF {b_code} - LABORATORY NO DUES CERTIFICATE"
            ))

            cert_id = f"SCET/ND/{datetime.now().strftime('%Y%m%d')}/{enroll}"
            info_data = [
                [f"Certificate ID: {cert_id}", f"Issue Date: {datetime.now().strftime('%d-%b-%Y %I:%M:%S %p')}"],
                [f"Student Name: {student_name}", f"Enrollment No: {enroll}"],
                [f"Academic Branch: {student_branch}", "Status: VERIFIED & APPROVED"]
            ]
            t_info = Table(info_data, colWidths=[260, 260])
            t_info.setStyle(TableStyle([
                ('PADDING', (0,0), (-1,-1), 5),
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
                ('FONTSIZE', (0,0), (-1,-1), 9.5),
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
                ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
            ]))
            elements.append(t_info)
            elements.append(Spacer(1, 18))

            cert_text = (
                f"This is to certify that <b>{student_name}</b> (Enrollment No: <b>{enroll}</b>) has returned all "
                f"borrowed laboratory hardware components, apparatus, and testing kits. There are <b>NO OUTSTANDING DUES</b> "
                f"or unpaid overdue penalties recorded in any laboratories under the Department of <b>{b_code}</b>."
            )
            elements.append(Paragraph(cert_text, ParagraphStyle("CertBody", parent=styles["Normal"], fontSize=10, leading=15, textColor=colors.HexColor("#1E293B"))))

            doc.build(elements)
            messagebox.showinfo("Clearance Approved", f"Official No Dues Certificate PDF generated successfully:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate PDF:\n{e}")

    def close_student_history_tab(self):
        if hasattr(self, "current_history_tab") and self.current_history_tab:
            try:
                self.tabview.delete(self.current_history_tab)
            except Exception:
                pass
            self.current_history_tab = None
            self.current_history_query = None
            if hasattr(self, "history_table"):
                del self.history_table
            self.tabview.set("Issue & Return Ledger")

    def search_student_history_dialog(self):
        query = simpledialog.askstring("Student Lookup", "Enter Student Name or Enrollment Number:", parent=self)
        if not query:
            return
        query_clean = query.strip()
        search_pattern = f"%{query_clean.upper()}%"

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT issue_id, enrollment_no, student_name, NVL(student_branch, '-'), NVL(student_mobile, '-'), comp_id,
                       TO_CHAR(issue_date, 'YYYY-MM-DD HH:MI:SS AM') AS idate,
                       NVL(TO_CHAR(due_date, 'YYYY-MM-DD HH:MI:SS AM'), '-') AS ddate,
                       issue_qty, status,
                       NVL(TO_CHAR(return_date, 'YYYY-MM-DD HH:MI:SS AM'), 'Pending') AS rdate,
                       returned_qty, issued_by, NVL(return_condition, 'Pending'),
                       penalty_fee, NVL(student_email, '-'), TRUNC(SYSDATE - due_date) AS days_late
                FROM issue_records
                WHERE UPPER(student_name) LIKE :1 OR UPPER(enrollment_no) LIKE :2
                ORDER BY issue_id DESC
            """, (search_pattern, search_pattern))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if not rows:
                messagebox.showinfo("Student Lookup", f"No borrowing history found for: '{query_clean}'.")
                return

            self.open_student_history_tab(rows, query_clean, search_pattern)

        except Exception as e:
            messagebox.showerror("Database Error", str(e))

    def open_student_history_tab(self, rows, query_clean, search_pattern):
        matched_name = rows[0][2]
        matched_enroll = rows[0][1]
        self.current_history_query = search_pattern
        tab_name = f"👤 {matched_name} ({matched_enroll})"

        if hasattr(self, "current_history_tab") and self.current_history_tab:
            try:
                self.tabview.delete(self.current_history_tab)
            except Exception:
                pass

        self.current_history_tab = tab_name
        tab_frame = self.tabview.add(tab_name)
        self.tabview.set(tab_name)

        container = ctk.CTkFrame(tab_frame, fg_color="transparent")
        container.pack(fill="both", expand=True)

        right_panel = ctk.CTkFrame(container, fg_color=THEME["card_bg"], border_width=1, border_color=THEME["card_border"], corner_radius=12)
        right_panel.pack(fill="both", expand=True, pady=5)

        banner = ctk.CTkFrame(right_panel, fg_color="#EFF6FF", corner_radius=8, height=44)
        banner.pack(fill="x", padx=15, pady=(12, 6))

        info_txt = f"Borrowing Ledger: {matched_name}  •  Enrollment: {matched_enroll}  •  Total Records: {len(rows)}"
        ctk.CTkLabel(banner, text=info_txt, font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"), text_color=THEME["primary"]).pack(side="left", padx=14, pady=8)

        ctk.CTkButton(
            banner,
            text="✕ Close Tab",
            width=90,
            height=28,
            fg_color=THEME["accent_red"],
            hover_color=THEME["accent_red_hover"],
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.close_student_history_tab
        ).pack(side="right", padx=10, pady=8)

        op_bar = ctk.CTkFrame(right_panel, fg_color="transparent")
        op_bar.pack(fill="x", padx=15, pady=(4, 8))

        ctk.CTkButton(
            op_bar,
            text="↩️ Mark Returned",
            fg_color=THEME["accent_green"],
            hover_color=THEME["accent_green_hover"],
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32,
            command=self.process_return_item
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            op_bar,
            text="✏️ Edit Record",
            fg_color=THEME["accent_gold"],
            hover_color=THEME["accent_gold_hover"],
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32,
            command=lambda: self.open_issue_modal(edit_mode=True)
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            op_bar,
            text="🗑️ Delete Record",
            fg_color=THEME["accent_red"],
            hover_color=THEME["accent_red_hover"],
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32,
            command=self.delete_selected_issue_record
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            op_bar,
            text="🖨️ Gate-Pass PDF",
            fg_color=THEME["input_bg"],
            hover_color=THEME["card_highlight"],
            text_color=THEME["primary"],
            border_width=1,
            border_color=THEME["card_border"],
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32,
            command=self.generate_gate_pass_receipt
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            op_bar,
            text="🎓 Generate No Dues",
            fg_color=THEME["input_bg"],
            hover_color=THEME["card_highlight"],
            text_color=THEME["accent_gold"],
            border_width=1,
            border_color=THEME["card_border"],
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32,
            command=lambda: self.generate_student_no_dues_certificate(matched_enroll)
        ).pack(side="left", padx=4)

        table_frame = ctk.CTkFrame(right_panel, fg_color="transparent")
        table_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        cols = ("issue_id", "enrollment_no", "student_name", "student_branch", "student_mobile", "comp_id", "issue_date", "due_date", "issue_qty", "status", "return_date", "returned_qty", "issued_by", "return_condition", "penalty_fee", "student_email")
        self.history_table = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")

        for c in cols:
            self.history_table.heading(c, text=c.replace("_", " ").title())
            self.history_table.column(c, width=95, minwidth=80, anchor="center")

        self.history_table.column("student_name", width=140, minwidth=110, anchor="w")
        self.history_table.column("comp_id", width=95, minwidth=80)
        self.history_table.column("enrollment_no", width=115, minwidth=90)
        self.history_table.column("issue_date", width=165, minwidth=140)
        self.history_table.column("due_date", width=165, minwidth=140)
        self.history_table.column("return_date", width=165, minwidth=140)
        self.history_table.column("return_condition", width=110, minwidth=90)
        self.history_table.column("penalty_fee", width=120, minwidth=100)
        self.history_table.column("student_email", width=160, minwidth=130, anchor="w")

        v_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.history_table.yview)
        h_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.history_table.xview)
        self.history_table.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.history_table.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        self.history_table.bind("<Double-1>", lambda e: self.open_issue_modal(edit_mode=True))
        self.populate_custom_history_rows(rows)

    def populate_custom_history_rows(self, rows):
        if not hasattr(self, "history_table"):
            return

        for r in self.history_table.get_children():
            self.history_table.delete(r)

        for row in rows:
            row_list = list(row[:16])
            status = str(row_list[9]).upper()
            penalty_val = row_list[14]
            days_late = row[16] if len(row) > 16 and row[16] is not None else 0

            if status == "ISSUED" and days_late > 0:
                row_list[9] = f"OVERDUE (+{int(days_late)}d)"

            if "RETURNED" in status:
                if penalty_val is not None and penalty_val > 0:
                    row_list[14] = f"₹{penalty_val:.2f}"
                else:
                    row_list[14] = "ON TIME"
            else:
                if days_late > 0:
                    row_list[14] = f"₹{int(days_late) * 10:.2f} (Accruing)"
                else:
                    row_list[14] = "ON TIME"

            self.history_table.insert("", "end", values=row_list)

    def refresh_student_history_table(self):
        if not hasattr(self, "history_table") or not getattr(self, "current_history_query", None):
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT issue_id, enrollment_no, student_name, NVL(student_branch, '-'), NVL(student_mobile, '-'), comp_id,
                       TO_CHAR(issue_date, 'YYYY-MM-DD HH:MI:SS AM') AS idate,
                       NVL(TO_CHAR(due_date, 'YYYY-MM-DD HH:MI:SS AM'), '-') AS ddate,
                       issue_qty, status,
                       NVL(TO_CHAR(return_date, 'YYYY-MM-DD HH:MI:SS AM'), 'Pending') AS rdate,
                       returned_qty, issued_by, NVL(return_condition, 'Pending'),
                       penalty_fee, NVL(student_email, '-'), TRUNC(SYSDATE - due_date) AS days_late
                FROM issue_records
                WHERE UPPER(student_name) LIKE :1 OR UPPER(enrollment_no) LIKE :2
                ORDER BY issue_id DESC
            """, (self.current_history_query, self.current_history_query))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if rows:
                self.populate_custom_history_rows(rows)
            else:
                self.close_student_history_tab()
        except Exception:
            pass

    def toggle_overdue_filter(self):
        self.show_only_overdue = not self.show_only_overdue
        if self.show_only_overdue:
            self.overdue_filter_btn.configure(fg_color=THEME["accent_red"], text="Showing Overdue Only")
        else:
            self.overdue_filter_btn.configure(fg_color="#F1F5F9", text="Filter Overdue Records")
        self.populate_issue_table()

    def launch_main_portal(self, role):
        if role == "admin" and not self.verify_scet_network():
            return

        self.current_role = role
        self.clear_window()

        header = ctk.CTkFrame(self, height=75, corner_radius=0, fg_color=THEME["card_bg"], border_width=1, border_color=THEME["card_border"])
        header.pack(fill="x", side="top")

        if hasattr(self, "su_logo_header") and self.su_logo_header:
            ctk.CTkLabel(header, image=self.su_logo_header, text="").pack(side="left", padx=(18, 8), pady=8)

        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left", padx=5)

        ctk.CTkLabel(
            title_frame,
            text="SCET Lab Tracker",
            text_color=THEME["primary"],
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold")
        ).pack(anchor="w")

        badge_text = "● ADMIN ACTIVE" if role == "admin" else "● STUDENT BROWSE"
        badge_color = THEME["accent_gold"] if role == "admin" else THEME["text_muted"]
        ctk.CTkLabel(
            title_frame,
            text=badge_text,
            text_color=badge_color,
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold")
        ).pack(anchor="w")

        nav_ctrl = ctk.CTkFrame(header, fg_color=THEME["input_bg"], corner_radius=10, border_width=1, border_color=THEME["card_border"])
        nav_ctrl.pack(side="left", padx=15, pady=10)

        ctk.CTkLabel(nav_ctrl, text="Branch:", text_color=THEME["text_muted"], font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(10, 4))
        ctk.CTkLabel(nav_ctrl, text=self.selected_branch.get(), text_color=THEME["text_main"], font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 10), pady=6)

        stats_bar = ctk.CTkFrame(header, fg_color="transparent")
        stats_bar.pack(side="left", padx=10)

        self.stat_items_lbl = ctk.CTkLabel(stats_bar, text="Total Items: 0", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["primary"])
        self.stat_items_lbl.pack(side="left", padx=6)

        self.stat_stock_lbl = ctk.CTkLabel(stats_bar, text="Stock Units: 0", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["accent_green"])
        self.stat_stock_lbl.pack(side="left", padx=6)

        self.stat_issued_lbl = ctk.CTkLabel(stats_bar, text="Active Issued: 0", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["accent_gold"])
        self.stat_issued_lbl.pack(side="left", padx=6)

        self.stat_overdue_lbl = ctk.CTkLabel(stats_bar, text="Overdue: 0", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["accent_red"])
        self.stat_overdue_lbl.pack(side="left", padx=6)

        if hasattr(self, "scet_logo_header") and self.scet_logo_header:
            ctk.CTkLabel(header, image=self.scet_logo_header, text="").pack(side="right", padx=(8, 18), pady=8)

        ctk.CTkButton(
            header,
            text="Sign Out",
            width=85,
            height=34,
            fg_color=THEME["accent_red"],
            hover_color=THEME["accent_red_hover"],
            corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.show_login_screen
        ).pack(side="right", padx=(4, 10), pady=13)

        def handle_back_navigation():
            if self.current_role == "admin":
                self.show_branch_selection_screen()
            else:
                self.show_login_screen()

        ctk.CTkButton(
            header,
            text="⬅ Back",
            width=75,
            height=34,
            fg_color=THEME["input_bg"],
            hover_color=THEME["card_highlight"],
            text_color=THEME["text_main"],
            border_width=1,
            border_color=THEME["card_border"],
            corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=handle_back_navigation
        ).pack(side="right", padx=(0, 6), pady=13)

        self.tabview = ctk.CTkTabview(
            self,
            fg_color=THEME["bg_main"],
            segmented_button_selected_color=THEME["primary"],
            segmented_button_selected_hover_color=THEME["primary_hover"],
            segmented_button_unselected_color=THEME["card_border"],
            segmented_button_unselected_hover_color="#CBD5E1",
            text_color=THEME["text_main"]
        )
        self.tabview.pack(fill="both", expand=True, padx=20, pady=10)

        self.tab_inventory = self.tabview.add("Component Inventory")
        self.build_inventory_tab()

        if self.current_role == "admin":
            self.tab_issues = self.tabview.add("Issue & Return Ledger")
            self.build_issues_tab()

        self.update_live_stats()

    def build_inventory_tab(self):
        container = ctk.CTkFrame(self.tab_inventory, fg_color="transparent")
        container.pack(fill="both", expand=True)

        right_view = ctk.CTkFrame(
            container,
            fg_color=THEME["card_bg"],
            border_width=1,
            border_color=THEME["card_border"],
            corner_radius=12
        )
        right_view.pack(fill="both", expand=True, pady=5)

        search_bar = ctk.CTkFrame(right_view, fg_color="transparent")
        search_bar.pack(fill="x", padx=15, pady=12)

        self.inv_search = ctk.CTkEntry(
            search_bar,
            placeholder_text="Search component by name, ID, or category...",
            fg_color=THEME["input_bg"],
            border_color=THEME["card_border"],
            text_color=THEME["text_main"],
            corner_radius=8,
            height=36
        )
        self.inv_search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.inv_search.bind("<Return>", lambda e: self.search_inventory())

        ctk.CTkButton(
            search_bar,
            text="Search",
            width=80,
            height=36,
            fg_color=THEME["primary"],
            hover_color=THEME["primary_hover"],
            corner_radius=8,
            command=self.search_inventory
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            search_bar,
            text="Reset",
            width=70,
            height=36,
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color=THEME["primary"],
            corner_radius=8,
            command=self.reset_inventory_search
        ).pack(side="left", padx=3)

        if self.current_role == "admin":
            self.inv_actions_var = ctk.StringVar(value="⚡ Manage Inventory")
            self.inv_actions_menu = ctk.CTkOptionMenu(
                search_bar,
                values=[
                    "+ Add New Component",
                    "✏️ Edit Selected Component",
                    "🗑️ Delete Selected Component",
                    "📑 Low-Stock Reorder Slip",
                    "💾 Database Snapshot Backup"
                ],
                variable=self.inv_actions_var,
                command=self.handle_inventory_dropdown_action,
                width=175,
                height=36,
                fg_color=THEME["primary"],
                button_color=THEME["primary_hover"],
                button_hover_color=THEME["hero_gradient_dark"],
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=8
            )
            self.inv_actions_menu.pack(side="left", padx=3)

            ctk.CTkButton(
                search_bar,
                text="📑 Stock PDF",
                width=100,
                height=36,
                fg_color=THEME["accent_green"],
                hover_color=THEME["accent_green_hover"],
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=8,
                command=self.export_inventory_pdf
            ).pack(side="left", padx=3)

        table_frame = ctk.CTkFrame(right_view, fg_color="transparent")
        table_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        cols = ("comp_id", "comp_name", "category", "specifications", "branch_code", "total_qty", "issued_qty", "available_qty", "photo")
        self.inv_table = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")

        self.inv_table.heading("comp_id", text="Item Code")
        self.inv_table.heading("comp_name", text="Component Name")
        self.inv_table.heading("category", text="Category")
        self.inv_table.heading("specifications", text="Specs")
        self.inv_table.heading("branch_code", text="Branch")
        self.inv_table.heading("total_qty", text="Total")
        self.inv_table.heading("issued_qty", text="Issued")
        self.inv_table.heading("available_qty", text="Available")
        self.inv_table.heading("photo", text="Photo")

        self.inv_table.column("comp_id", width=110, minwidth=90)
        self.inv_table.column("comp_name", width=220, minwidth=160)
        self.inv_table.column("category", width=140, minwidth=100)
        self.inv_table.column("specifications", width=180, minwidth=120)
        self.inv_table.column("branch_code", width=85, minwidth=65, anchor="center")
        self.inv_table.column("total_qty", width=85, minwidth=60, anchor="center")
        self.inv_table.column("issued_qty", width=85, minwidth=60, anchor="center")
        self.inv_table.column("available_qty", width=95, minwidth=70, anchor="center")
        self.inv_table.column("photo", width=140, minwidth=110, anchor="center")

        inv_v_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.inv_table.yview)
        inv_h_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.inv_table.xview)
        self.inv_table.configure(yscrollcommand=inv_v_scroll.set, xscrollcommand=inv_h_scroll.set)

        self.inv_table.grid(row=0, column=0, sticky="nsew")
        inv_v_scroll.grid(row=0, column=1, sticky="ns")
        inv_h_scroll.grid(row=1, column=0, sticky="ew")

        self.inv_table.bind("<Double-1>", self.on_product_double_click)

        self.populate_inventory_table()

    def on_product_double_click(self, event):
        selected = self.inv_table.selection()
        if not selected:
            return
        row = self.inv_table.item(selected[0])["values"]
        cid = str(row[0])
        cname = str(row[1])
        b_code = self.selected_branch.get()

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT category, specifications, total_qty, issued_qty, available_qty
                FROM lab_components
                WHERE comp_id = :1 AND branch_code = :2
            """, (cid, b_code))
            data = cursor.fetchone()
            cursor.close()
            conn.close()

            if not data:
                return

            cat, specs, tot, iss, avl = data
            all_images = self.get_component_images(cid, b_code)

            modal = ctk.CTkToplevel(self)
            modal.title(f"Component Specifications & Gallery - {cname}")
            modal.geometry("500x650")
            modal.grab_set()

            ctk.CTkLabel(modal, text=cname, font=ctk.CTkFont(size=18, weight="bold"), text_color=THEME["primary"]).pack(pady=(14, 2))
            ctk.CTkLabel(modal, text=f"Item Code: {cid}  •  Department: {b_code}", font=ctk.CTkFont(size=11), text_color=THEME["text_muted"]).pack(pady=(0, 6))

            card = ctk.CTkFrame(modal, height=235, fg_color=THEME["card_bg"], border_width=1, border_color=THEME["card_border"], corner_radius=12)
            card.pack(pady=6, padx=18, fill="x")

            img_label = ctk.CTkLabel(card, text="")
            img_label.pack(expand=True, pady=6)

            nav_frame = ctk.CTkFrame(modal, fg_color="transparent")
            nav_frame.pack(fill="x", padx=20, pady=(2, 6))

            curr_idx = [0]
            counter_lbl = ctk.CTkLabel(nav_frame, text="", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["text_muted"])

            def render_image():
                if not all_images:
                    img_label.configure(image=None, text="No Images Uploaded for this Component")
                    counter_lbl.configure(text="No Photos")
                    btn_prev.configure(state="disabled")
                    btn_next.configure(state="disabled")
                    return
                try:
                    pil_img = Image.open(io.BytesIO(all_images[curr_idx[0]]))
                    pil_img.thumbnail((360, 210))
                    ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)
                    img_label.configure(image=ctk_img, text="")
                    counter_lbl.configure(text=f"Photo {curr_idx[0] + 1} of {len(all_images)}")
                except Exception:
                    img_label.configure(image=None, text="[Image Attached - Unsupported Format]")
                    counter_lbl.configure(text=f"Photo {curr_idx[0] + 1} of {len(all_images)}")

                btn_prev.configure(state="normal" if curr_idx[0] > 0 else "disabled")
                btn_next.configure(state="normal" if curr_idx[0] < len(all_images) - 1 else "disabled")

            def prev_img():
                if curr_idx[0] > 0:
                    curr_idx[0] -= 1
                    render_image()

            def next_img():
                if curr_idx[0] < len(all_images) - 1:
                    curr_idx[0] += 1
                    render_image()

            btn_prev = ctk.CTkButton(nav_frame, text="◀ Previous", width=95, height=28, fg_color=THEME["primary"], hover_color=THEME["primary_hover"], command=prev_img)
            btn_prev.pack(side="left")

            counter_lbl.pack(side="left", expand=True)

            btn_next = ctk.CTkButton(nav_frame, text="Next ▶", width=95, height=28, fg_color=THEME["primary"], hover_color=THEME["primary_hover"], command=next_img)
            btn_next.pack(side="right")

            render_image()

            details_card = ctk.CTkFrame(modal, fg_color=THEME["card_bg"], border_width=1, border_color=THEME["card_border"], corner_radius=12)
            details_card.pack(pady=6, padx=18, fill="x")

            details = [
                ("Category", cat),
                ("Specifications", specs or "-"),
                ("Total Quantity", f"{tot} Units"),
                ("Issued Quantity", f"{iss} Units"),
                ("Available Quantity", f"{avl} Units")
            ]

            for label, val in details:
                row_f = ctk.CTkFrame(details_card, fg_color="transparent")
                row_f.pack(fill="x", padx=14, pady=2.5)
                ctk.CTkLabel(row_f, text=label + ":", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["text_muted"]).pack(side="left")
                ctk.CTkLabel(row_f, text=str(val), font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["text_main"]).pack(side="right")

            status_text = f"● IN STOCK ({avl} AVAILABLE)" if avl > 0 else "● CURRENTLY OUT OF STOCK"
            status_color = THEME["accent_green"] if avl > 0 else THEME["accent_red"]
            ctk.CTkLabel(modal, text=status_text, font=ctk.CTkFont(size=12, weight="bold"), text_color=status_color).pack(pady=(4, 6))

            ctk.CTkButton(
                modal,
                text="Close Preview",
                fg_color=THEME["primary"],
                hover_color=THEME["primary_hover"],
                font=ctk.CTkFont(size=12, weight="bold"),
                width=160,
                height=34,
                corner_radius=8,
                command=modal.destroy
            ).pack(pady=(0, 10))

        except Exception as e:
            messagebox.showerror("Database Error", str(e))

    def handle_inventory_dropdown_action(self, choice):
        self.inv_actions_var.set("⚡ Manage Inventory")
        if choice == "+ Add New Component":
            self.open_component_modal(edit_mode=False)
        elif choice == "✏️ Edit Selected Component":
            self.open_component_modal(edit_mode=True)
        elif choice == "🗑️ Delete Selected Component":
            self.delete_selected_component()
        elif choice == "📑 Low-Stock Reorder Slip":
            self.generate_procurement_slip()
        elif choice == "💾 Database Snapshot Backup":
            self.export_oracle_database_backup()

    def open_component_modal(self, edit_mode=False):
        sel_row = None
        images_list = []
        image_modified = [False]

        if edit_mode:
            selected = self.inv_table.selection()
            if not selected:
                messagebox.showwarning("Warning", "Please select a component row from the table to edit.")
                return
            sel_row = self.inv_table.item(selected[0])["values"]
            images_list = self.get_component_images(sel_row[0], self.selected_branch.get())

        modal = ctk.CTkToplevel(self)
        modal.title("Edit Component" if edit_mode else "Add New Component")
        modal.geometry("520x670")
        modal.grab_set()

        title_lbl = "Edit Component Details" if edit_mode else "Add New Hardware Component"
        ctk.CTkLabel(modal, text=title_lbl, font=ctk.CTkFont(size=16, weight="bold"), text_color=THEME["primary"]).pack(pady=(12, 6))

        scroll = ctk.CTkScrollableFrame(modal, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=15, pady=5)

        e_name = self.create_styled_entry(scroll, "Component Name")
        e_cat = self.create_styled_entry(scroll, "Category (e.g. Sensor, Board)")
        e_spec = self.create_styled_entry(scroll, "Specifications")
        e_total = self.create_styled_entry(scroll, "Total Quantity")

        img_frame = ctk.CTkFrame(scroll, fg_color=THEME["input_bg"], corner_radius=10, border_width=1, border_color=THEME["card_border"])
        img_frame.pack(fill="x", padx=10, pady=8)

        ctk.CTkLabel(img_frame, text="Component Photos (Add Multiple Allowed):", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["text_muted"]).pack(anchor="w", padx=10, pady=(8, 4))

        thumb_container = ctk.CTkScrollableFrame(img_frame, height=95, orientation="horizontal", fg_color="transparent")
        thumb_container.pack(fill="x", padx=10, pady=4)

        status_counter_lbl = ctk.CTkLabel(img_frame, text="", font=ctk.CTkFont(size=11), text_color=THEME["text_muted"])
        status_counter_lbl.pack(pady=2)

        def refresh_thumbnails():
            for w in thumb_container.winfo_children():
                w.destroy()

            status_counter_lbl.configure(text=f"{len(images_list)} photo(s) selected")

            for idx, img_bytes in enumerate(images_list):
                try:
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    pil_img.thumbnail((60, 60))
                    ctk_thumb = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)

                    t_card = ctk.CTkFrame(thumb_container, fg_color=THEME["card_bg"], corner_radius=6, border_width=1, border_color=THEME["card_border"])
                    t_card.pack(side="left", padx=4, pady=2)

                    ctk.CTkLabel(t_card, image=ctk_thumb, text="").pack(padx=4, pady=(4, 2))

                    def remove_this(i=idx):
                        del images_list[i]
                        image_modified[0] = True
                        refresh_thumbnails()

                    ctk.CTkButton(t_card, text="✕", width=22, height=18, fg_color=THEME["accent_red"], hover_color=THEME["accent_red_hover"], font=ctk.CTkFont(size=9, weight="bold"), command=remove_this).pack(pady=(0, 3))
                except Exception:
                    pass

        refresh_thumbnails()

        def add_multiple_images():
            paths = filedialog.askopenfilenames(
                parent=modal,
                title="Select Component Image(s)",
                filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.webp;*.bmp")]
            )
            if not paths:
                return
            for p in paths:
                try:
                    with open(p, "rb") as f:
                        images_list.append(f.read())
                    image_modified[0] = True
                except Exception as ex:
                    messagebox.showerror("Error", f"Failed to load image {p}:\n{ex}", parent=modal)
            refresh_thumbnails()

        def clear_all():
            images_list.clear()
            image_modified[0] = True
            refresh_thumbnails()

        btn_row = ctk.CTkFrame(img_frame, fg_color="transparent")
        btn_row.pack(pady=(4, 8))

        ctk.CTkButton(
            btn_row,
            text="📁 Add Image(s)",
            fg_color=THEME["primary"],
            hover_color=THEME["primary_hover"],
            font=ctk.CTkFont(size=11, weight="bold"),
            height=30,
            width=135,
            corner_radius=6,
            command=add_multiple_images
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_row,
            text="🗑️ Clear All",
            fg_color=THEME["accent_red"],
            hover_color=THEME["accent_red_hover"],
            font=ctk.CTkFont(size=11, weight="bold"),
            height=30,
            width=110,
            corner_radius=6,
            command=clear_all
        ).pack(side="left", padx=4)

        if edit_mode and sel_row:
            e_name.insert(0, sel_row[1])
            e_cat.insert(0, sel_row[2])
            e_spec.insert(0, sel_row[3] if sel_row[3] != '-' else '')
            e_total.insert(0, sel_row[5])

        def save_item():
            cname = e_name.get().strip()
            ccat = e_cat.get().strip()
            cspec = e_spec.get().strip()
            total_raw = e_total.get().strip()
            b_code = self.selected_branch.get()

            if not cname or not ccat or not total_raw:
                messagebox.showerror("Validation Error", "Component Name, Category, and Total Quantity are strictly required.", parent=modal)
                return

            if not total_raw.isdigit() or int(total_raw) <= 0:
                messagebox.showerror("Validation Error", "Total Quantity must be a valid positive whole number.", parent=modal)
                return

            total = int(total_raw)

            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                if edit_mode and sel_row:
                    cid = sel_row[0]
                else:
                    clean_name = re.sub(r'[^a-zA-Z]', '', cname).upper()
                    prefix_tag = clean_name[:3] if len(clean_name) >= 3 else (clean_name + 'X' * (3 - len(clean_name)))
                    prefix_pattern = f"COMP-{prefix_tag}-%"

                    cursor.execute("""
                        SELECT comp_id FROM lab_components 
                        WHERE branch_code = :1 AND comp_id LIKE :2
                    """, (b_code, prefix_pattern))
                    existing_ids = [row[0] for row in cursor.fetchall()]

                    max_seq = 0
                    for ex_id in existing_ids:
                        try:
                            seq_part = int(ex_id.split('-')[-1])
                            if seq_part > max_seq:
                                max_seq = seq_part
                        except ValueError:
                            pass

                    next_seq = max_seq + 1
                    cid = f"COMP-{prefix_tag}-{next_seq:03d}"

                cursor.execute("SELECT issued_qty FROM lab_components WHERE comp_id = :1 AND branch_code = :2", (cid, b_code))
                existing = cursor.fetchone()

                if existing:
                    issued = existing[0]
                    if total < issued:
                        messagebox.showerror("Validation Error", f"Total quantity cannot be less than currently issued units ({issued}).", parent=modal)
                        cursor.close()
                        conn.close()
                        return

                    new_avail = total - issued
                    cursor.execute("""
                        UPDATE lab_components
                        SET comp_name = :1, category = :2, specifications = :3,
                            total_qty = :4, available_qty = :5
                        WHERE comp_id = :6 AND branch_code = :7
                    """, (cname, ccat, cspec, total, new_avail, cid, b_code))

                    if image_modified[0]:
                        cursor.execute("DELETE FROM component_images WHERE comp_id = :1 AND branch_code = :2", (cid, b_code))
                        for b_img in images_list:
                            cursor.execute("INSERT INTO component_images (comp_id, branch_code, image_data) VALUES (:1, :2, :3)", (cid, b_code, b_img))

                    conn.commit()
                    messagebox.showinfo("Success", f"Component '{cid}' updated successfully.", parent=modal)
                else:
                    cursor.execute("""
                        INSERT INTO lab_components (comp_id, comp_name, category, specifications, branch_code, total_qty, issued_qty, available_qty)
                        VALUES (:1, :2, :3, :4, :5, :6, 0, :7)
                    """, (cid, cname, ccat, cspec, b_code, total, total))

                    for b_img in images_list:
                        cursor.execute("INSERT INTO component_images (comp_id, branch_code, image_data) VALUES (:1, :2, :3)", (cid, b_code, b_img))

                    conn.commit()
                    messagebox.showinfo("Success", f"New component added with auto-generated ID: '{cid}'", parent=modal)

                cursor.close()
                conn.close()

                self.populate_inventory_table()
                self.refresh_issue_dropdown()
                modal.destroy()
            except Exception as e:
                messagebox.showerror("Database Error", str(e), parent=modal)

        btn_txt = "Save Changes" if edit_mode else "Add Component"
        ctk.CTkButton(
            modal,
            text=btn_txt,
            fg_color=THEME["accent_green"],
            hover_color=THEME["accent_green_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            corner_radius=8,
            command=save_item
        ).pack(fill="x", padx=25, pady=(8, 14))

    def create_styled_entry(self, parent, placeholder):
        entry = ctk.CTkEntry(
            parent,
            placeholder_text=placeholder,
            fg_color=THEME["input_bg"],
            border_color=THEME["card_border"],
            text_color=THEME["text_main"],
            corner_radius=8,
            height=34
        )
        entry.pack(fill="x", padx=10, pady=4)
        return entry

    def populate_inventory_table(self, query=""):
        for row in self.inv_table.get_children():
            self.inv_table.delete(row)

        b_code = self.selected_branch.get()
        if not b_code:
            return

        if self.current_role == "student":
            try:
                resp = requests.get(
                    f"{STUDENT_API_BASE}/api/branches/{b_code}/components",
                    params={"q": query} if query else None,
                    timeout=STUDENT_API_TIMEOUT
                )
                resp.raise_for_status()
                for item in resp.json():
                    cid, cname, ccat, cspec = item["comp_id"], item["comp_name"], item["category"], item["specifications"]
                    tot, iss, avl, img_count = item["total_qty"], item["issued_qty"], item["available_qty"], item["image_count"]
                    if img_count > 1:
                        p_status = f"Available ({img_count})"
                    elif img_count == 1:
                        p_status = "Available"
                    else:
                        p_status = "Not Available"

                    row_vals = (cid, cname, ccat, cspec or "-", b_code, tot, iss, avl, p_status)
                    self.inv_table.insert("", "end", values=row_vals)
                self.update_live_stats()
            except Exception as e:
                messagebox.showerror("Connection Error", f"Could not reach the lab catalog server:\n{e}")
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            base_sql = """
                SELECT c.comp_id, c.comp_name, c.category, c.specifications, c.branch_code, c.total_qty, c.issued_qty, c.available_qty,
                       (SELECT COUNT(*) FROM component_images ci WHERE ci.comp_id = c.comp_id AND ci.branch_code = c.branch_code) AS img_count
                FROM lab_components c
                WHERE c.branch_code = :1
            """
            params = [b_code]

            if query:
                q = f"%{query.lower()}%"
                base_sql += " AND (LOWER(c.comp_id) LIKE :2 OR LOWER(c.comp_name) LIKE :3 OR LOWER(c.category) LIKE :4)"
                params.extend([q, q, q])

            base_sql += " ORDER BY c.comp_id"
            cursor.execute(base_sql, params)

            for item in cursor.fetchall():
                cid, cname, ccat, cspec, bcode, tot, iss, avl, img_count = item
                if img_count > 1:
                    p_status = f"Available ({img_count})"
                elif img_count == 1:
                    p_status = "Available"
                else:
                    p_status = "Not Available"

                row_vals = (cid, cname, ccat, cspec or "-", bcode, tot, iss, avl, p_status)
                self.inv_table.insert("", "end", values=row_vals)

            cursor.close()
            conn.close()
            self.update_live_stats()
        except Exception as e:
            messagebox.showerror("Database Error", f"Could not load components:\n{e}")

    def search_inventory(self):
        query = self.inv_search.get().strip()
        self.populate_inventory_table(query)

    def reset_inventory_search(self):
        self.inv_search.delete(0, "end")
        self.populate_inventory_table()

    def delete_selected_component(self):
        selected = self.inv_table.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select a component row to delete.")
            return

        cid = self.inv_table.item(selected[0])["values"][0]
        b_code = self.selected_branch.get()
        if not messagebox.askyesno("Confirm Deletion", f"Permanently delete component '{cid}' from [{b_code}] and its transaction history?"):
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM component_images WHERE comp_id = :1 AND branch_code = :2", (cid, b_code))
            cursor.execute("DELETE FROM issue_records WHERE comp_id = :1 AND branch_code = :2", (cid, b_code))
            cursor.execute("DELETE FROM lab_components WHERE comp_id = :1 AND branch_code = :2", (cid, b_code))
            conn.commit()
            cursor.close()
            conn.close()

            self.reindex_issue_ids()
            self.populate_inventory_table()
            self.refresh_issue_dropdown()
            messagebox.showinfo("Deleted", f"Component '{cid}' removed.")
        except Exception as e:
            messagebox.showerror("Database Error", str(e))

    def on_enrollment_type(self, event=None):
        enroll = self.iss_enroll.get().strip()
        if enroll and "@" not in self.iss_semail.get():
            self.iss_semail.delete(0, "end")
            self.iss_semail.insert(0, f"{enroll.lower()}@scet.ac.in")

    def validate_issue_fields(self, enroll, sname, sbranch, smobile, semail, qty_str, days_str):
        if not enroll or not sname or not sbranch or not smobile or not semail:
            messagebox.showerror("Validation Error", "All fields are required. Enrollment No, Student Name, Branch, Mobile No, and Email cannot be empty.")
            return False, 0, 0

        if not re.match(r"^\d{10}$", smobile):
            messagebox.showerror("Validation Error", "Invalid Mobile Number. It must contain exactly 10 digits without spaces or country code.")
            return False, 0, 0

        email_pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        if not re.match(email_pattern, semail):
            messagebox.showerror("Validation Error", "Invalid Email Address format. Please enter a valid email (e.g., student@scet.ac.in).")
            return False, 0, 0

        if not qty_str.isdigit() or int(qty_str) <= 0:
            messagebox.showerror("Validation Error", "Quantity must be a positive integer (greater than 0).")
            return False, 0, 0

        if not days_str:
            days_str = "0"

        if not days_str.isdigit() or int(days_str) < 0:
            messagebox.showerror("Validation Error", "Duration (Days) must be 0 or a positive whole number.")
            return False, 0, 0

        return True, int(qty_str), int(days_str)

    def build_issues_tab(self):
        container = ctk.CTkFrame(self.tab_issues, fg_color="transparent")
        container.pack(fill="both", expand=True)

        right_issue = ctk.CTkFrame(container, fg_color=THEME["card_bg"], border_width=1, border_color=THEME["card_border"], corner_radius=12)
        right_issue.pack(fill="both", expand=True, pady=5)

        toolbar = ctk.CTkFrame(right_issue, fg_color="transparent")
        toolbar.pack(fill="x", padx=15, pady=12)

        self.overdue_filter_btn = ctk.CTkButton(
            toolbar,
            text="Filter Overdue Records",
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color=THEME["accent_red"],
            border_width=1,
            border_color=THEME["card_border"],
            font=ctk.CTkFont(size=12, weight="bold"),
            height=36,
            corner_radius=8,
            command=self.toggle_overdue_filter
        )
        self.overdue_filter_btn.pack(side="left", padx=(0, 6))

        self.issue_actions_var = ctk.StringVar(value="⚡ Manage Issues")
        self.issue_actions_menu = ctk.CTkOptionMenu(
            toolbar,
            values=[
                "+ Issue New Component",
                "✏️ Edit Selected Record",
                "↩️ Mark Selected Returned",
                "🗑️ Delete Selected Record",
                "🖨️ Print Gate-Pass PDF",
                "🔍 Lookup Student History",
                "📧 Batch Email Overdue Alerts",
                "🎓 Generate No Dues PDF",
                "⚡ Simulate Overdue Record"
            ],
            variable=self.issue_actions_var,
            command=self.handle_issue_dropdown_action,
            width=185,
            height=36,
            fg_color=THEME["primary"],
            button_color=THEME["primary_hover"],
            button_hover_color=THEME["hero_gradient_dark"],
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=8
        )
        self.issue_actions_menu.pack(side="left", padx=4)

        table_frame_issue = ctk.CTkFrame(right_issue, fg_color="transparent")
        table_frame_issue.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        table_frame_issue.grid_rowconfigure(0, weight=1)
        table_frame_issue.grid_columnconfigure(0, weight=1)

        cols = ("issue_id", "enrollment_no", "student_name", "student_branch", "student_mobile", "comp_id", "issue_date", "due_date", "issue_qty", "status", "return_date", "returned_qty", "issued_by", "return_condition", "penalty_fee", "student_email")
        self.issue_table = ttk.Treeview(table_frame_issue, columns=cols, show="headings", selectmode="browse")

        for c in cols:
            header_title = "Due Date" if c == "due_date" else c.replace("_", " ").title()
            self.issue_table.heading(c, text=header_title)
            self.issue_table.column(c, width=95, minwidth=80, anchor="center")

        self.issue_table.column("student_name", width=140, minwidth=110, anchor="w")
        self.issue_table.column("comp_id", width=95, minwidth=80)
        self.issue_table.column("enrollment_no", width=115, minwidth=90)
        self.issue_table.column("issue_date", width=165, minwidth=140)
        self.issue_table.column("due_date", width=165, minwidth=140)
        self.issue_table.column("return_date", width=165, minwidth=140)
        self.issue_table.column("return_condition", width=110, minwidth=90)
        self.issue_table.column("penalty_fee", width=120, minwidth=100)
        self.issue_table.column("student_email", width=160, minwidth=130, anchor="w")

        issue_v_scroll = ttk.Scrollbar(table_frame_issue, orient="vertical", command=self.issue_table.yview)
        issue_h_scroll = ttk.Scrollbar(table_frame_issue, orient="horizontal", command=self.issue_table.xview)
        self.issue_table.configure(yscrollcommand=issue_v_scroll.set, xscrollcommand=issue_h_scroll.set)

        self.issue_table.grid(row=0, column=0, sticky="nsew")
        issue_v_scroll.grid(row=0, column=1, sticky="ns")
        issue_h_scroll.grid(row=1, column=0, sticky="ew")

        self.issue_table.bind("<Double-1>", lambda e: self.open_issue_modal(edit_mode=True))
        self.populate_issue_table()

    def handle_issue_dropdown_action(self, choice):
        self.issue_actions_var.set("⚡ Manage Issues")
        if choice == "+ Issue New Component":
            self.open_issue_modal(edit_mode=False)
        elif choice == "✏️ Edit Selected Record":
            self.open_issue_modal(edit_mode=True)
        elif choice == "↩️ Mark Selected Returned":
            self.process_return_item()
        elif choice == "🗑️ Delete Selected Record":
            self.delete_selected_issue_record()
        elif choice == "🖨️ Print Gate-Pass PDF":
            self.generate_gate_pass_receipt()
        elif choice == "🔍 Lookup Student History":
            self.search_student_history_dialog()
        elif choice == "📧 Batch Email Overdue Alerts":
            self.batch_email_overdue_reminders()
        elif choice == "🎓 Generate No Dues PDF":
            self.generate_student_no_dues_certificate()
        elif choice == "⚡ Simulate Overdue Record":
            self.simulate_fast_forward_overdue_record()

    def open_issue_modal(self, edit_mode=False):
        target_table = self.get_active_issue_table()
        sel_row = None
        if edit_mode and target_table:
            selected = target_table.selection()
            if not selected:
                messagebox.showwarning("Warning", "Please select an issue record from the table to edit.")
                return
            sel_row = target_table.item(selected[0])["values"]
            self.active_edit_issue_id = sel_row[0]
        else:
            self.active_edit_issue_id = None

        modal = ctk.CTkToplevel(self)
        modal.title("Edit Issue Record" if edit_mode else "Checkout / Issue Hardware")
        modal.geometry("480x620")
        modal.grab_set()

        title_lbl = f"Edit Issue Record #{self.active_edit_issue_id}" if edit_mode else "New Hardware Checkout"
        ctk.CTkLabel(modal, text=title_lbl, font=ctk.CTkFont(size=16, weight="bold"), text_color=THEME["primary"]).pack(pady=(15, 8))

        scroll = ctk.CTkScrollableFrame(modal, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=15, pady=5)

        self.iss_enroll = self.create_styled_entry(scroll, "Student Enrollment No")
        self.iss_enroll.bind("<KeyRelease>", self.on_enrollment_type)

        self.iss_sname = self.create_styled_entry(scroll, "Student Name")
        self.iss_sbranch = self.create_styled_entry(scroll, "Student Branch (e.g. CO)")
        self.iss_smobile = self.create_styled_entry(scroll, "Mobile No (10 Digits)")
        self.iss_semail = self.create_styled_entry(scroll, "Student Email (e.g. student@scet.ac.in)")

        self.comp_map = self.get_components_map()
        comp_options = list(self.comp_map.keys()) if self.comp_map else ["No Components Available"]
        self.iss_comp_var = ctk.StringVar(value=comp_options[0])

        ctk.CTkLabel(scroll, text="Select Hardware Component:", font=ctk.CTkFont(size=11, weight="bold"), text_color=THEME["text_muted"]).pack(anchor="w", padx=10, pady=(6, 2))
        self.iss_dropdown = ctk.CTkOptionMenu(
            scroll,
            values=comp_options,
            variable=self.iss_comp_var,
            fg_color=THEME["input_bg"],
            text_color=THEME["text_main"],
            button_color=THEME["primary"],
            button_hover_color=THEME["primary_hover"],
            corner_radius=8,
            height=36
        )
        self.iss_dropdown.pack(fill="x", padx=10, pady=(0, 6))

        self.iss_qty = self.create_styled_entry(scroll, "Quantity to Issue")
        self.iss_days = self.create_styled_entry(scroll, "Issue Duration (Days)")

        if edit_mode and sel_row:
            self.iss_enroll.delete(0, "end"); self.iss_enroll.insert(0, sel_row[1])
            self.iss_sname.delete(0, "end"); self.iss_sname.insert(0, sel_row[2])
            self.iss_sbranch.delete(0, "end"); self.iss_sbranch.insert(0, sel_row[3] if sel_row[3] != '-' else '')
            self.iss_smobile.delete(0, "end"); self.iss_smobile.insert(0, sel_row[4] if sel_row[4] != '-' else '')
            self.iss_qty.delete(0, "end"); self.iss_qty.insert(0, sel_row[8])
            self.iss_semail.delete(0, "end"); self.iss_semail.insert(0, sel_row[15] if len(sel_row) > 15 and sel_row[15] != '-' else '')
            target_cid = str(sel_row[5]).upper()
            for k, v in self.comp_map.items():
                if v == target_cid:
                    self.iss_comp_var.set(k)
                    break

        action_btn_text = "Save Record Updates" if edit_mode else "Confirm Issuance"
        action_btn_color = THEME["accent_gold"] if edit_mode else THEME["primary"]
        action_btn_hover = THEME["accent_gold_hover"] if edit_mode else THEME["primary_hover"]
        action_cmd = (lambda: self.process_update_issue_record(modal)) if edit_mode else (lambda: self.process_issue_item(modal))

        ctk.CTkButton(
            modal,
            text=action_btn_text,
            fg_color=action_btn_color,
            hover_color=action_btn_hover,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=40,
            corner_radius=8,
            command=action_cmd
        ).pack(fill="x", padx=25, pady=(8, 14))

    def get_components_map(self):
        b_code = self.selected_branch.get()
        if not b_code:
            return {}

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT comp_id, comp_name FROM lab_components WHERE branch_code = :1 ORDER BY comp_name", (b_code,))
            mapping = {f"{row[1]} ({row[0]})": row[0] for row in cursor.fetchall()}
            cursor.close()
            conn.close()
            return mapping
        except Exception:
            return {}

    def refresh_issue_dropdown(self):
        if hasattr(self, "iss_dropdown"):
            self.comp_map = self.get_components_map()
            opts = list(self.comp_map.keys()) if self.comp_map else ["No Components Available"]
            self.iss_dropdown.configure(values=opts)
            self.iss_comp_var.set(opts[0])

    def populate_issue_table(self):
        if not hasattr(self, "issue_table"):
            return

        for row in self.issue_table.get_children():
            self.issue_table.delete(row)

        b_code = self.selected_branch.get()
        if not b_code:
            return

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            sql = """
                SELECT issue_id, enrollment_no, student_name, NVL(student_branch, '-'), NVL(student_mobile, '-'), comp_id,
                       TO_CHAR(issue_date, 'YYYY-MM-DD HH:MI:SS AM') AS idate,
                       NVL(TO_CHAR(due_date, 'YYYY-MM-DD HH:MI:SS AM'), '-') AS ddate,
                       issue_qty, status,
                       NVL(TO_CHAR(return_date, 'YYYY-MM-DD HH:MI:SS AM'), 'Pending') AS rdate,
                       returned_qty, issued_by, NVL(return_condition, 'Pending'),
                       penalty_fee, NVL(student_email, '-'), TRUNC(SYSDATE - due_date) AS days_late
                FROM issue_records 
                WHERE branch_code = :1
            """
            params = [b_code]

            if self.show_only_overdue:
                sql += " AND status = 'ISSUED' AND due_date < SYSDATE"

            sql += " ORDER BY issue_id DESC"
            cursor.execute(sql, params)
            
            for row in cursor.fetchall():
                row_list = list(row[:16])
                status = str(row_list[9]).upper()
                penalty_val = row_list[14]
                days_late = row[16] if row[16] is not None else 0

                if status == "ISSUED" and days_late > 0:
                    row_list[9] = f"OVERDUE (+{int(days_late)}d)"

                if "RETURNED" in status:
                    if penalty_val is not None and penalty_val > 0:
                        row_list[14] = f"₹{penalty_val:.2f}"
                    else:
                        row_list[14] = "ON TIME"
                else:
                    if days_late > 0:
                        row_list[14] = f"₹{int(days_late) * 10:.2f} (Accruing)"
                    else:
                        row_list[14] = "ON TIME"

                self.issue_table.insert("", "end", values=row_list)
            cursor.close()
            conn.close()
            self.update_live_stats()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load issue records:\n{e}")

    def process_update_issue_record(self, modal=None):
        if not self.active_edit_issue_id:
            messagebox.showwarning("Warning", "Please select an issue record to edit.")
            return

        enroll = self.iss_enroll.get().strip()
        sname = self.iss_sname.get().strip()
        sbranch = self.iss_sbranch.get().strip()
        smobile = self.iss_smobile.get().strip()
        semail = self.iss_semail.get().strip()
        comp_id = self.comp_map.get(self.iss_comp_var.get())
        b_code = self.selected_branch.get()

        valid, new_qty, days = self.validate_issue_fields(
            enroll, sname, sbranch, smobile, semail, self.iss_qty.get().strip(), self.iss_days.get().strip()
        )
        if not valid:
            return

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT issue_qty, status, comp_id FROM issue_records WHERE issue_id = :1", (self.active_edit_issue_id,))
            record = cursor.fetchone()
            if not record:
                messagebox.showerror("Error", "Selected record no longer exists.")
                return

            old_qty, status, old_comp_id = record[0], str(record[1]).upper(), record[2]

            if "ISSUED" in status or "OVERDUE" in status:
                qty_diff = new_qty - old_qty
                if qty_diff > 0:
                    cursor.execute("""
                        UPDATE lab_components
                        SET issued_qty = issued_qty + :1, available_qty = available_qty - :2
                        WHERE comp_id = :3 AND branch_code = :4 AND available_qty >= :5
                    """, (qty_diff, qty_diff, comp_id, b_code, qty_diff))
                    if cursor.rowcount == 0:
                        messagebox.showwarning("Stock Error", f"Not enough available units for extra quantity (+{qty_diff}).")
                        return
                elif qty_diff < 0:
                    cursor.execute("""
                        UPDATE lab_components
                        SET issued_qty = issued_qty - :1, available_qty = available_qty + :2
                        WHERE comp_id = :3 AND branch_code = :4
                    """, (abs(qty_diff), abs(qty_diff), comp_id, b_code))

            cursor.execute("""
                UPDATE issue_records
                SET enrollment_no = :1, student_name = :2, student_branch = :3, student_mobile = :4,
                    comp_id = :5, issue_qty = :6, due_date = SYSDATE + :7, student_email = :8
                WHERE issue_id = :9
            """, (enroll, sname, sbranch, smobile, comp_id, new_qty, days, semail, self.active_edit_issue_id))

            conn.commit()
            self.populate_issue_table()
            self.populate_inventory_table()
            self.refresh_student_history_table()
            messagebox.showinfo("Updated", f"Issue Record #{self.active_edit_issue_id} updated successfully.")
            self.active_edit_issue_id = None
            if modal:
                modal.destroy()
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Database Error", str(e))
        finally:
            cursor.close()
            conn.close()

    def process_issue_item(self, modal=None):
        enroll = self.iss_enroll.get().strip()
        sname = self.iss_sname.get().strip()
        sbranch = self.iss_sbranch.get().strip()
        smobile = self.iss_smobile.get().strip()
        semail = self.iss_semail.get().strip()
        comp_id = self.comp_map.get(self.iss_comp_var.get())
        b_code = self.selected_branch.get()

        valid, qty, days = self.validate_issue_fields(
            enroll, sname, sbranch, smobile, semail, self.iss_qty.get().strip(), self.iss_days.get().strip()
        )
        if not valid:
            return

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM issue_records
                WHERE enrollment_no = :1 AND status = 'ISSUED'
            """, (enroll,))
            active_count = cursor.fetchone()[0]

            if active_count >= 3:
                messagebox.showwarning(
                    "Borrow Limit Reached",
                    f"Student {enroll} already has {active_count} active issued items. Max limit is 3 items."
                )
                return

            cursor.execute("""
                UPDATE lab_components
                SET issued_qty = issued_qty + :1, available_qty = available_qty - :2
                WHERE comp_id = :3 AND branch_code = :4 AND available_qty >= :5
            """, (qty, qty, comp_id, b_code, qty))

            if cursor.rowcount == 0:
                messagebox.showwarning("Out of Stock", f"Not enough available units for {comp_id} in [{b_code}].")
                return

            cursor.execute("SELECT NVL(MAX(issue_id), 0) + 1 FROM issue_records")
            next_id = cursor.fetchone()[0]

            cursor.execute("""
                INSERT INTO issue_records (issue_id, enrollment_no, student_name, student_branch, student_mobile, comp_id, branch_code, issue_date, due_date, issue_qty, status, return_date, returned_qty, issued_by, student_email, return_condition, penalty_fee)
                VALUES (:1, :2, :3, :4, :5, :6, :7, SYSDATE, SYSDATE + :8, :9, 'ISSUED', NULL, 0, :10, :11, 'Pending', 0)
            """, (next_id, enroll, sname, sbranch, smobile, comp_id, b_code, days, qty, self.current_user or "admin", semail))

            cursor.execute("SELECT TO_CHAR(issue_date, 'YYYY-MM-DD HH:MI:SS AM'), TO_CHAR(due_date, 'YYYY-MM-DD HH:MI:SS AM') FROM issue_records WHERE issue_id = :1", (next_id,))
            time_row = cursor.fetchone()
            issue_time_str = time_row[0]
            return_time_str = time_row[1]

            conn.commit()
            
            self.trigger_auto_issuance_email(next_id, sname, enroll, sbranch, smobile, comp_id, qty, days, semail, issue_time_str, return_time_str)

            self.populate_issue_table()
            self.populate_inventory_table()
            self.refresh_student_history_table()
            if modal:
                modal.destroy()
            messagebox.showinfo("Success", f"Issued {qty} unit(s) of {comp_id} to {sname} for {days} day(s).\nConfirmation email with PDF Gate Pass dispatched to {semail}.")
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Database Error", str(e))
        finally:
            cursor.close()
            conn.close()

    def process_return_item(self):
        target_table = self.get_active_issue_table()
        if not target_table:
            return

        selected = target_table.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select an active issue record to return.")
            return

        row_vals = target_table.item(selected[0])["values"]
        issue_id = row_vals[0]
        enroll = row_vals[1]
        sname = row_vals[2]
        comp_id = row_vals[5]
        qty = int(row_vals[8])
        status = str(row_vals[9]).upper()
        semail = row_vals[15] if len(row_vals) > 15 and row_vals[15] != '-' else ''
        b_code = self.selected_branch.get()

        if "RETURNED" in status:
            messagebox.showinfo("Notice", "This component has already been returned.")
            return

        fine_msg = ""
        penalty_amount = 0.0
        if "OVERDUE" in status:
            try:
                days_overdue = int(status.split("+")[1].replace("D)", "").replace("d)", "").strip())
                penalty_amount = float(days_overdue * 10)
                fine_msg = f"\n[NOTICE: Item is {days_overdue} day(s) late. Late penalty fine: ₹{penalty_amount:.2f}]"
            except Exception:
                fine_msg = "\n[NOTICE: Item is returned past due date.]"

        condition = simpledialog.askstring(
            "Component Return Inspection",
            f"Enter hardware condition for {comp_id}:{fine_msg}\n(Options: Working, Damaged, Burnt, Lost)",
            parent=self,
            initialvalue="Working"
        )
        if not condition:
            return

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE lab_components
                SET issued_qty = issued_qty - :1, available_qty = available_qty + :2
                WHERE comp_id = :3 AND branch_code = :4
            """, (qty, qty, comp_id, b_code))

            cursor.execute("""
                UPDATE issue_records
                SET status = 'RETURNED', return_date = SYSDATE, returned_qty = :1, return_condition = :2, penalty_fee = :3
                WHERE issue_id = :4
            """, (qty, condition.strip(), penalty_amount, issue_id))

            conn.commit()

            if semail:
                penalty_str = f"₹{penalty_amount:.2f}" if penalty_amount > 0 else "ON TIME (₹0.00)"
                ret_subject = f"Component Returned Confirmation - SCET Lab [{comp_id}]"
                ret_body = (
                    f"Dear {sname},\n\n"
                    f"Greetings from Sarvajanik College of Engineering & Technology (SCET)!\n\n"
                    f"This is to confirm that your borrowed hardware component has been successfully returned:\n\n"
                    f"  • Transaction Pass : SCET-GP-{issue_id}\n"
                    f"  • Component Code   : {comp_id} (Quantity: {qty} Unit(s))\n"
                    f"  • Return Date/Time : {datetime.now().strftime('%d-%b-%Y %I:%M:%S %p')}\n"
                    f"  • Return Condition : {condition}\n"
                    f"  • Penalty Status   : {penalty_str}\n\n"
                    f"Your lab records have been updated accordingly.\n\n"
                    f"Best Regards,\n"
                    f"Laboratory In-Charge & Faculty Staff\n"
                    f"Sarvajanik College of Engineering & Technology (SCET), Surat"
                )
                self.send_async_email(semail, ret_subject, ret_body)

            self.populate_issue_table()
            self.populate_inventory_table()
            self.refresh_student_history_table()
            messagebox.showinfo("Returned", f"{qty} unit(s) marked as returned ({condition}). Stock restored.{fine_msg}")
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error", str(e))
        finally:
            cursor.close()
            conn.close()

    def delete_selected_issue_record(self):
        target_table = self.get_active_issue_table()
        if not target_table:
            return

        selected = target_table.selection()
        if not selected:
            messagebox.showwarning("Warning", "Please select a ledger record to delete.")
            return

        row_vals = target_table.item(selected[0])["values"]
        issue_id = row_vals[0]
        comp_id = row_vals[5]
        qty = int(row_vals[8])
        status = str(row_vals[9]).upper()
        b_code = self.selected_branch.get()

        if not messagebox.askyesno("Confirm Record Deletion", f"Permanently delete Issue Record #{issue_id} from database?"):
            return

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            if "ISSUED" in status or "OVERDUE" in status:
                cursor.execute("""
                    UPDATE lab_components
                    SET issued_qty = issued_qty - :1, available_qty = available_qty + :2
                    WHERE comp_id = :3 AND branch_code = :4
                """, (qty, qty, comp_id, b_code))

            cursor.execute("DELETE FROM issue_records WHERE issue_id = :1", (issue_id,))
            conn.commit()
            cursor.close()
            conn.close()

            self.reindex_issue_ids()

            self.populate_issue_table()
            self.populate_inventory_table()
            self.refresh_student_history_table()
            messagebox.showinfo("Deleted", f"Record #{issue_id} removed and all ledger IDs renumbered seamlessly.")
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Database Error", str(e))


if __name__ == "__main__":
    start_student_api_in_background()
    app = SCETLabManager()
    app.mainloop()