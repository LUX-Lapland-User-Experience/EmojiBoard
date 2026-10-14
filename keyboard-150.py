# Version 1.5.0 (Raspberry Pi)
# Changes from 1.4.0:
#  - NEW: inserting a USB stick automatically copies keypress_log.csv to it
#    (background thread, signals success/failure with beeps + LED flash)
#  - Writes to the log file are now protected by a lock (copy never reads a half-written line)
#  - Removed dead duplicate functions that had ended up outside the class
#  - main(): no more NameError in the finally block if setup fails
import RPi.GPIO as GPIO
import time
import random
import string
import os
import json
import queue
import shutil
import threading
import subprocess
from datetime import datetime
from rpi_ws281x import PixelStrip, Color, WS2811_STRIP_RGB
import logging

# Set up logging
logging.basicConfig(
    filename='keyboard_debug.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# GPIO pin assignments
ROW_PINS = [0, 1, 2]
COL_PINS = [3, 4, 5, 6, 7, 8, 9]
EXTRA_ROW_PINS = [10, 11]
EXTRA_COL_PINS = [12, 13]
SPEAKER_PIN = 16
NEO_PIN = 21
NUM_PIXELS = 26

# Results file (relative to the working directory, as before)
LOG_FILE = "keypress_log.csv"

# --- USB copy settings ---
USB_MOUNT_POINT = "/mnt/emojiboard-usb"   # where the stick is mounted temporarily
USB_POLL_SECONDS = 1.0                    # how often to look for a new stick
USB_COPY_NAME = "keypress_log_{timestamp}.csv"  # name on the stick; {timestamp} avoids overwriting
USB_FEEDBACK_BEEP = True                  # beep when copy succeeds / fails
USB_FEEDBACK_LED = True                   # flash all LEDs green (ok) / red (error)

# Colors - Explicitly define RGB values for WS2811
RED = Color(255, 0, 0)      # Pure red
GREEN = Color(0, 255, 0)    # Pure green
BLUE = Color(0, 0, 255)     # Pure blue
WHITE = Color(255, 255, 255)  # White

# Define colors for different states
SELECTED_COLOR = GREEN
RETURN_COLOR = GREEN
BACKSPACE_COLOR = RED
DEFAULT_COLOR = WHITE

# Button and key mappings (same as before)
BUTTON_MAP_3x7 = [
    [1, 2, 3, 4, 5, 6, 7],
    [8, 9, 10, 11, 12, 13, 14],
    [15, 16, 17, 18, 19, 20, 21]
]
BUTTON_MAP_2x2 = [
    [22, 23],
    [24, 25]
]

PIXEL_MAP_3x7 = [
    [1, 2, 3, 4, 5, 6, 7],
    [17, 16, 15, 14, 13, 12, 11],
    [18, 19, 20, 21, 22, 23, 24]
]
PIXEL_MAP_2x2 = [
    [8, 9],
    [25, 26]
]

KEY_MAP_3x7 = [
    ["A", "B", "C", "D", "E", "F", "G"],
    ["I", "J", "K", "L", "M", "N", "O"],
    ["P", "Q", "R", "S", "T", "U", "V"]
]
KEY_MAP_2x2 = [
    ["H", "RETURN"],
    ["X", "BACKSPACE"]
]


# ---------------------------------------------------------------------------
# USB auto-copy
# ---------------------------------------------------------------------------
def parse_usb_partitions(lsblk_json):
    """Return a list of (device_path, mountpoint_or_None) for filesystems on USB disks.

    Takes the JSON output of: lsblk -J -o NAME,PATH,TYPE,TRAN,FSTYPE,MOUNTPOINT
    Only disks whose transport is 'usb' are considered, so the SD card is never touched.
    """
    found = []
    for disk in json.loads(lsblk_json).get("blockdevices", []):
        if disk.get("tran") != "usb":
            continue
        children = [c for c in (disk.get("children") or []) if c.get("fstype")]
        if children:
            for part in children:
                found.append((part["path"], part.get("mountpoint")))
        elif disk.get("fstype"):  # stick formatted without a partition table
            found.append((disk["path"], disk.get("mountpoint")))
    return found


class UsbCopier(threading.Thread):
    """Watches for newly inserted USB sticks and copies the results file to them.

    Runs in its own thread so the keypad scanning loop is never blocked.
    Results are reported through `events` (a queue) and handled by the main thread,
    because only the main thread touches the speaker and the LEDs.
    """

    def __init__(self, log_lock, events):
        super().__init__(daemon=True, name="UsbCopier")
        self.log_lock = log_lock
        self.events = events
        self.handled = set()  # devices already copied to, until they are removed
        self.sudo = [] if os.geteuid() == 0 else ["sudo"]

    def run(self):
        logging.info("USB copier started")
        while True:
            try:
                present = self.list_partitions()
                paths = {path for path, _ in present}
                for path, mountpoint in present:
                    if path not in self.handled:
                        self.handled.add(path)
                        self.copy_to_stick(path, mountpoint)
                self.handled &= paths  # forget sticks that were removed
            except Exception as e:
                logging.error(f"USB copier error: {e}")
            time.sleep(USB_POLL_SECONDS)

    def list_partitions(self):
        out = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,PATH,TYPE,TRAN,FSTYPE,MOUNTPOINT"],
            capture_output=True, text=True, timeout=10, check=True
        ).stdout
        return parse_usb_partitions(out)

    def copy_to_stick(self, device, mountpoint):
        logging.info(f"USB stick detected: {device}")
        time.sleep(1.5)  # let the system finish detecting the stick

        mounted_by_us = False
        try:
            src = os.path.abspath(LOG_FILE)
            if not os.path.exists(src):
                self.events.put(("error", f"{LOG_FILE} does not exist yet, nothing to copy"))
                return

            if not mountpoint:  # not auto-mounted (e.g. Raspberry Pi OS Lite)
                os.makedirs(USB_MOUNT_POINT, exist_ok=True)
                subprocess.run(self.sudo + ["mount", device, USB_MOUNT_POINT],
                               capture_output=True, text=True, timeout=20, check=True)
                mountpoint = USB_MOUNT_POINT
                mounted_by_us = True

            name = USB_COPY_NAME.format(timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"))
            dest = os.path.join(mountpoint, name)

            with self.log_lock:  # don't copy while a session is being written
                shutil.copyfile(src, dest)
            os.sync()

            if os.path.getsize(dest) != os.path.getsize(src):
                raise IOError("copied file size does not match the original")

            logging.info(f"Copied {src} -> {dest}")
            self.events.put(("ok", f"Copied to {dest}"))
        except subprocess.CalledProcessError as e:
            self.events.put(("error", f"mount failed: {(e.stderr or '').strip()}"))
        except Exception as e:
            self.events.put(("error", f"copy failed: {e}"))
        finally:
            if mounted_by_us:
                try:
                    subprocess.run(self.sudo + ["umount", USB_MOUNT_POINT],
                                   capture_output=True, text=True, timeout=20, check=True)
                except Exception as e:
                    logging.error(f"umount failed: {e}")


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------
class KeyboardRecorder:
    def __init__(self):
        self.current_session_keys = []
        self.selected_keys = set()
        self.current_user_id = self.generate_user_id()
        self.strip = None
        self.log_lock = threading.Lock()
        self.usb_events = queue.Queue()
        self.setup_neopixels()
        self.setup_audio()
        self.last_keypress_time = 0
        self.debounce_delay = 0.2
        self.matrix_state = [[False for _ in COL_PINS] for _ in ROW_PINS]
        self.extra_matrix_state = [[False for _ in EXTRA_COL_PINS] for _ in EXTRA_ROW_PINS]

    def setup_audio(self):
        """Initialize PWM for the speaker"""
        try:
            self.pwm = GPIO.PWM(SPEAKER_PIN, 440)  # Start with 440 Hz (A4 note)
            self.pwm.start(0)  # Start with 0% duty cycle (silent)
            logging.info("Audio PWM initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize audio PWM: {e}")
            raise

    def play_key_beep(self):
        """Play a short beep for regular key presses"""
        try:
            self.pwm.ChangeFrequency(440)  # A4 note
            self.pwm.ChangeDutyCycle(50)
            time.sleep(0.05)  # Short duration
            self.pwm.ChangeDutyCycle(0)
        except Exception as e:
            logging.error(f"Error playing key beep: {e}")

    def play_success_sound(self):
        """Play a success melody when Return is pressed"""
        try:
            # Play an ascending arpeggio
            notes = [
                (392, 0.1),  # G4
                (493.88, 0.1),  # B4
                (587.33, 0.1),  # D5
                (783.99, 0.2)   # G5
            ]

            for freq, duration in notes:
                self.pwm.ChangeFrequency(freq)
                self.pwm.ChangeDutyCycle(50)
                time.sleep(duration)

            self.pwm.ChangeDutyCycle(0)
        except Exception as e:
            logging.error(f"Error playing success sound: {e}")

    def play_error_sound(self):
        """Two low beeps, used when the USB copy fails"""
        try:
            for _ in range(2):
                self.pwm.ChangeFrequency(220)  # A3 note
                self.pwm.ChangeDutyCycle(50)
                time.sleep(0.25)
                self.pwm.ChangeDutyCycle(0)
                time.sleep(0.1)
        except Exception as e:
            logging.error(f"Error playing error sound: {e}")

    def setup_neopixels(self):
        try:
            # Initialize with explicit WS2811 RGB strip type
            self.strip = PixelStrip(NUM_PIXELS, NEO_PIN, strip_type=WS2811_STRIP_RGB)
            self.strip.begin()
            self.set_default_colors()
            logging.info("NeoPixels initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize NeoPixels: {e}")
            raise

    def generate_user_id(self):
        adjectives = ["Quick", "Happy", "Bright", "Calm", "Sharp", "Brave", "Smart", "Kind", "Loyal", "Wise"]
        animals = ["Fox", "Bear", "Wolf", "Eagle", "Tiger", "Lion", "Deer", "Owl", "Hawk", "Shark"]
        return f"{random.choice(adjectives)}{random.choice(animals)}{random.randint(1000,9999)}"

    def handle_keypress(self, key):
        current_time = time.time()

        if current_time - self.last_keypress_time < self.debounce_delay:
            return

        self.last_keypress_time = current_time
        logging.debug(f"Key pressed: {key}")

        if key == "BACKSPACE":
            self.play_key_beep()
            self.selected_keys.clear()
            self.set_default_colors()
        elif key == "RETURN":
            if self.selected_keys:  # Only play success sound if there are selected keys
                self.play_success_sound()
            self.current_session_keys = list(self.selected_keys)
            self.save_session()
            self.selected_keys.clear()
            self.current_session_keys = []
            self.current_user_id = self.generate_user_id()
            self.set_default_colors()
        else:
            self.play_key_beep()
            self.toggle_key(key)

    def find_pixel_index(self, key):
        for row_idx, row in enumerate(KEY_MAP_3x7):
            if key in row:
                col_idx = row.index(key)
                return PIXEL_MAP_3x7[row_idx][col_idx] - 1
        for row_idx, row in enumerate(KEY_MAP_2x2):
            if key in row:
                col_idx = row.index(key)
                return PIXEL_MAP_2x2[row_idx][col_idx] - 1
        return -1

    def save_session(self):
        try:
            if self.selected_keys:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                session_data = f"{self.current_user_id},{''.join(sorted(self.selected_keys))},{timestamp}\n"

                with self.log_lock:
                    with open(LOG_FILE, "a") as log_file:
                        log_file.write(session_data)
                logging.info(f"Session saved: {session_data.strip()}")
        except Exception as e:
            logging.error(f"Error saving session: {e}")

    def scan_matrix(self):
        current_time = time.time()
        if (current_time - self.last_keypress_time) < self.debounce_delay:
            return

        # Reset all row pins to HIGH
        for pin in ROW_PINS + EXTRA_ROW_PINS:
            GPIO.output(pin, GPIO.HIGH)

        key_detected = False

        # Scan main matrix (3x7)
        for row_idx, row_pin in enumerate(ROW_PINS):
            # Set current row LOW
            GPIO.output(row_pin, GPIO.LOW)
            time.sleep(0.001)  # Allow signal to settle

            # Read all columns for this row
            for col_idx, col_pin in enumerate(COL_PINS):
                is_pressed = GPIO.input(col_pin) == GPIO.LOW

                # If this is a new keypress
                if is_pressed and not self.matrix_state[row_idx][col_idx]:
                    key = KEY_MAP_3x7[row_idx][col_idx]
                    self.handle_keypress(key)
                    key_detected = True
                    self.last_keypress_time = current_time

                # Update state
                self.matrix_state[row_idx][col_idx] = is_pressed

            # Set row back to HIGH
            GPIO.output(row_pin, GPIO.HIGH)

            if key_detected:
                return

        # Scan extra matrix (2x2) only if no key was detected in main matrix
        if not key_detected:
            for row_idx, row_pin in enumerate(EXTRA_ROW_PINS):
                GPIO.output(row_pin, GPIO.LOW)
                time.sleep(0.001)

                for col_idx, col_pin in enumerate(EXTRA_COL_PINS):
                    is_pressed = GPIO.input(col_pin) == GPIO.LOW

                    if is_pressed and not self.extra_matrix_state[row_idx][col_idx]:
                        key = KEY_MAP_2x2[row_idx][col_idx]
                        self.handle_keypress(key)
                        self.last_keypress_time = current_time

                    self.extra_matrix_state[row_idx][col_idx] = is_pressed

                GPIO.output(row_pin, GPIO.HIGH)

        time.sleep(0.001)  # Small delay at the end of scanning

    def set_default_colors(self):
        """Set all LEDs to their default states"""
        for i in range(NUM_PIXELS):
            self.strip.setPixelColor(i, DEFAULT_COLOR)

        return_pixel = self.find_pixel_index("RETURN")
        backspace_pixel = self.find_pixel_index("BACKSPACE")
        if return_pixel >= 0:
            self.strip.setPixelColor(return_pixel, RETURN_COLOR)
        if backspace_pixel >= 0:
            self.strip.setPixelColor(backspace_pixel, BACKSPACE_COLOR)

        self.strip.show()

    def toggle_key(self, key):
        """Toggle a key's selected state"""
        pixel_index = self.find_pixel_index(key)
        if pixel_index >= 0:
            if key in self.selected_keys:
                self.selected_keys.remove(key)
                self.strip.setPixelColor(pixel_index, DEFAULT_COLOR)
                logging.debug(f"Key {key} deselected, pixel {pixel_index} set to DEFAULT")
            else:
                self.selected_keys.add(key)
                self.strip.setPixelColor(pixel_index, SELECTED_COLOR)
                logging.debug(f"Key {key} selected, pixel {pixel_index} set to SELECTED")
            self.strip.show()

    # --- USB feedback (runs in the main thread) ---
    def restore_leds(self):
        """Default colours, plus any keys currently selected in the session"""
        self.set_default_colors()
        for key in self.selected_keys:
            pixel_index = self.find_pixel_index(key)
            if pixel_index >= 0:
                self.strip.setPixelColor(pixel_index, SELECTED_COLOR)
        self.strip.show()

    def flash_all(self, color, times=2):
        try:
            for _ in range(times):
                for i in range(NUM_PIXELS):
                    self.strip.setPixelColor(i, color)
                self.strip.show()
                time.sleep(0.25)
                for i in range(NUM_PIXELS):
                    self.strip.setPixelColor(i, Color(0, 0, 0))
                self.strip.show()
                time.sleep(0.15)
        finally:
            self.restore_leds()

    def handle_usb_events(self):
        """Report the result of any USB copy that finished since the last call"""
        while True:
            try:
                status, message = self.usb_events.get_nowait()
            except queue.Empty:
                return
            if status == "ok":
                logging.info(f"USB copy OK: {message}")
                if USB_FEEDBACK_BEEP:
                    self.play_success_sound()
                if USB_FEEDBACK_LED:
                    self.flash_all(GREEN)
            else:
                logging.error(f"USB copy FAILED: {message}")
                if USB_FEEDBACK_BEEP:
                    self.play_error_sound()
                if USB_FEEDBACK_LED:
                    self.flash_all(RED)


def setup_gpio():
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)  # Disable warnings

        # Setup row pins as outputs
        for pin in ROW_PINS + EXTRA_ROW_PINS:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)

        # Setup column pins as inputs with pull-up resistors
        for pin in COL_PINS + EXTRA_COL_PINS:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        GPIO.setup(SPEAKER_PIN, GPIO.OUT)
        logging.info("GPIO setup completed successfully")
    except Exception as e:
        logging.error(f"GPIO setup failed: {e}")
        raise


def main():
    keyboard = None
    try:
        setup_gpio()
        keyboard = KeyboardRecorder()
        UsbCopier(keyboard.log_lock, keyboard.usb_events).start()
        logging.info("Keyboard recorder initialized and running...")

        while True:
            keyboard.handle_usb_events()
            keyboard.scan_matrix()
            time.sleep(0.01)

    except KeyboardInterrupt:
        logging.info("Program terminated by user")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    finally:
        if keyboard is not None and hasattr(keyboard, 'pwm'):
            keyboard.pwm.stop()
        GPIO.cleanup()
        logging.info("GPIO cleanup completed")


if __name__ == "__main__":
    main()
