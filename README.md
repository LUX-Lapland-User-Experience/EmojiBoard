# EmojiBoard
Emojiboard documentation & build kit assets & instructions

    LUX
    Emojiboard
    v2.1
    Luukkonen Timo
    10-14-2026


Device and Program Documentation

Firmware: `keyboard-150.py` (v1.5.0), located in `/home/emojiboard`.

## OVERVIEW

The hardware setup includes a Raspberry Pi (version 3 B v1.2) connected to:

- A 3x7 + 2x2 button matrix.
- NeoPixel LEDs (26 units).
- A buzzer (connected to GPIO pin 16).
- The program logs keypresses into a CSV file.

The program is implemented in Python and uses libraries such as RPi.GPIO, rpi_ws281x, and datetime. It starts automatically when the device boots using a cron job and saves keypresses to the file keypress_log.csv located in the /home/emojiboard folder.


## COPYING KEYPRESS_LOG.CSV TO A USB STICK

### Automatic copy (default)

1. Make sure the Emojiboard is powered on and running (the LEDs are lit).
2. Insert a USB stick into the Raspberry Pi. FAT32 and exFAT sticks work.
3. Wait a few seconds for the signal:
    - **Green flash on all LEDs and the success melody:** the file was copied and the stick is safe to remove.
    - **Red flash on all LEDs and two low beeps:** the copy failed. See Troubleshooting below.
4. Remove the stick.

The file on the stick is named `keypress_log_YYYYMMDD_HHMMSS.csv` (the time of the copy), so earlier copies are never overwritten.

Notes:

- Each stick is copied to once per insertion. To copy again, remove the stick and reinsert it.
- If no session has been saved yet, there is no file to copy and the copy fails. Press some keys and RETURN once to create it.
- Copying does not interrupt the keypad. It runs in the background.

### Manual copy (fallback)

Use this if the automatic copy does not work.

1. Connect HDMI display and a regular keyboard to the Emojiboard.
2. Connect power cable.
3. Insert the USB stick into the Raspberry Pi.
4. Identify the USB stick's device name using the command:

        lsblk

    The stick is typically listed as /dev/sda1 (or /dev/sdb1).

5. Mount the USB stick (assuming it is /dev/sda1):

        sudo mount /dev/sda1 /mnt

6. Copy the log file to the USB stick (sudo is required, otherwise "Permission denied"):

        sudo cp /home/emojiboard/keypress_log.csv /mnt

7. Safely unmount the USB stick:

        sudo umount /mnt

8. Remove the USB stick from the device.

Always do step 7 before removing the stick. A stick pulled out while still mounted leaves a dead mount on /mnt that can make the automatic copy fail with "Input/output error". If that has happened, run `sudo umount /mnt` (or `sudo umount -l /mnt`) and try again.


## PROGRAM FUNCTIONALITY

### NEOPIXEL LED CONTROL

Each button in the matrix is linked to a specific NeoPixel LED. When a button is pressed, its corresponding NeoPixel lights up green and returns to its default state once the button is released. NeoPixel 10 is programmatically set to remain off at all times and is not used in the program.

### BUTTON MATRIX

The matrix is divided into two sections:

1. A 3x7 matrix.
2. A 2x2 matrix.

The buttons are assigned the following functions:

- BACKSPACE: Clears the keypresses in the current session.
- RETURN: Saves the current session data and starts a new session.
- Other buttons: Add the pressed key to the current session.
- Key to Emojis conversion table attached to this document.

### KEYPRESS LOGGING

The program logs each session in the following format:

- A username (randomly generated combination of an adjective, animal, and unique code).
- Keypresses.
- A timestamp of when the session is saved.

The data is stored in the file keypress_log.csv.

### USB COPY FEEDBACK

| Result | LEDs | Sound |
| --- | --- | --- |
| Copy succeeded | All LEDs flash green | Success melody |
| Copy failed | All LEDs flash red | Two low beeps |

The behaviour can be changed at the top of `keyboard-150.py`: `USB_FEEDBACK_BEEP`, `USB_FEEDBACK_LED` (on/off), `USB_COPY_NAME` (file name on the stick) and `USB_MOUNT_POINT`.


## AUTOMATIC PROGRAM STARTUP

The program is configured to run automatically on device boot using a cron job. Add the following line to the user's crontab file by running `crontab -e`:

    @reboot sudo python3 /home/emojiboard/keyboard-150.py &

Root privileges (sudo) are needed for the NeoPixels and for mounting USB sticks.

To check the current line, run `crontab -l`.


## TROUBLESHOOTING

If the program does not start or the NeoPixel LEDs do not function correctly:

1. Verify that all hardware connections are correct.
2. Ensure the required libraries are installed:

        pip3 install rpi_ws281x RPi.GPIO

3. Check /home/emojiboard/keyboard_debug.log
4. Check /var/log/syslog for error messages.

If the USB copy fails (red flash):

1. Show the latest USB entries in the debug log (the -a is needed because the log can contain non-text bytes):

        grep -a -i usb /home/emojiboard/keyboard_debug.log | tail -10

2. Common causes:
    - **"Input/output error" on /mnt/emojiboard-usb:** an old stick is still mounted on /mnt. Run `sudo umount /mnt`, then reinsert the stick.
    - **"unknown filesystem type":** the stick uses a format the Pi cannot read. Use a FAT32 or exFAT stick.
    - **"keypress_log.csv does not exist yet":** no session has been saved. Press some keys and RETURN once.
    - **"a password is required":** the program is not running as root. Start it with sudo (see Automatic program startup).
3. Remove the stick and reinsert it to try again. A stick that failed is not retried until it has been removed.


## CHANGES

- **v2.1 (firmware 1.5.0):** USB sticks are now copied to automatically. The manual USB instructions were moved to the top and corrected (folder is /home/emojiboard, sudo is needed for cp). Startup line updated to keyboard-150.py.
- **v2.0 (firmware 1.4.0):** Initial documentation.
