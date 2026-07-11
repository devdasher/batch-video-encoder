# Batch Video Encoder

> A modern batch video encoding application powered by **HandBrakeCLI**.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![PySide6](https://img.shields.io/badge/GUI-PySide6-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)
![Status](https://img.shields.io/badge/Status-Beta-orange.svg)

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/abeb481e-42ed-4f4d-b9f5-94d156fbac82" />

Batch Video Encoder is a desktop application that makes encoding multiple videos with **HandBrakeCLI** much easier.

Instead of configuring every video one by one inside the official HandBrake GUI, you can:

- Add many videos at once
- Select multiple videos
- Apply encoding settings to all selected videos
- Easily manage subtitle burn-in settings
- Manage audio settings for multiple files
- Preview generated HandBrakeCLI commands
- Override any generated command with custom CLI arguments

The application is designed for users who regularly encode large batches of videos and want a much faster workflow.

---

# Features

- Batch encoding
- Drag & Drop support
- Multi-selection queue
- Apply settings to selected videos
- Video encoder selection
- Resolution presets
- Video bitrate configuration
- Audio settings
- Subtitle Burn-In support
- ASS/SSA subtitle support
- Live encoding progress
- Detailed log viewer
- Command preview
- Light & Dark themes
- Save default settings
- Extra Arguments support
- Portable-friendly
- Powered by HandBrakeCLI

---

# Why not just use the official HandBrake GUI?

The official HandBrake GUI is an excellent encoder.

However, when working with **many videos**, it becomes difficult and time-consuming to:

- Apply identical settings to dozens of videos
- Burn subtitles into multiple files
- Configure audio tracks repeatedly
- Change settings for many queued videos at once

Batch Video Encoder was created specifically to simplify these repetitive tasks.

With Batch Video Encoder you can simply:

1. Add all videos.
2. Select one, several, or all videos.
3. Configure your encoding settings once.
4. Click **Apply Settings**.

Done.

This saves a significant amount of time when processing large video collections.

---

# Powered by HandBrakeCLI

**This application is NOT a video encoder by itself.**

It is a graphical interface built on top of **HandBrakeCLI**.

All actual encoding work is performed by HandBrakeCLI.

Without HandBrakeCLI, this application cannot encode videos.

---

# Download HandBrakeCLI

Download the latest official HandBrakeCLI from:

https://handbrake.fr/downloads2.php

Official website:

https://handbrake.fr/

After downloading:

- Extract HandBrakeCLI
- Place `HandBrakeCLI.exe` anywhere you like
- Select it inside the application using:

```
Browse...
```

or place it beside the application if you're using the portable version.

---

# Installation

## Requirements

- Windows 10 / Windows 11
- HandBrakeCLI
- Python 3.10+ (only if running from source)

---

## Install from source

Clone the repository:

```bash
git clone https://github.com/yourusername/batch-video-encoder.git

cd batch-video-encoder
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run:

```bash
python main.py
```

---

# Python Requirements

```
PySide6
natsort
```

Python version:

```
Python 3.10+
```

---

# How to Use

## 1. Select HandBrakeCLI

Browse to your `HandBrakeCLI.exe`.

---

## 2. Add videos

You can:

- Drag & Drop
- Add Files...

---

## 3. Select videos

Select one or more videos inside the queue.

---

## 4. Configure settings

Change any encoding settings in the left panel.

---

## 5. Apply settings

Click:

```
Apply Settings
```

The settings are copied to all selected videos.

---

## 6. Start encoding

Click:

```
Start Encoding
```

---

# Usage Examples

## Multiple Audio Tracks

If your video contains multiple audio tracks, several audio settings accept comma-separated values.

Example:

Audio Tracks

```
1,2
```

Audio Encoder

```
aac,aac
```

Audio Bitrate

```
128,224
```

Mixdown

```
stereo,5point1
```

or

```
stereo,6ch
```

Another example:

```
Audio Tracks
1,2,3

Audio Encoder
aac,aac,aac

Audio Bitrate
128,224,384

Mixdown
stereo,5point1,5point1
```

Each value corresponds to the matching audio track.

---

# Subtitle Burn-In

You can burn subtitles into your videos using:

- Subtitle Tracks
- SSA/ASS files
- Subtitle Burned
- SSA Burn

This makes batch subtitle encoding much easier than repeatedly configuring every file inside the official HandBrake GUI.

---

# Extra Arguments

Not every HandBrakeCLI option is exposed in the graphical interface.

For advanced users there is an **Extra Arguments** field.

Anything entered there is appended to the generated command.

Example:

```
--encoder-profile high
```

or

```
--deblock
```

or

```
--encoder-level 4.1
```

Extra Arguments can also **override** options already selected in the GUI.

This allows advanced users to use virtually any HandBrakeCLI feature without waiting for it to be added to the interface.

---

# Bug Report

If you encounter any issue, please open a GitHub Issue and include:

- operating system
- application version
- HandBrakeCLI version
- steps to reproduce
- screenshots (if possible)

Bug reports are greatly appreciated.

---

# Limitations

This application intentionally does **not** expose every single HandBrakeCLI option.

Only the most commonly used settings are available through the graphical interface.

Advanced users should use the **Extra Arguments** field whenever additional HandBrakeCLI options are required.

---

# AI-Assisted Development

This project was developed with the assistance of modern AI tools.

AI was used to help with:

- software architecture
- refactoring
- code generation
- documentation
- debugging
- UI improvements

All final implementation, testing, integration, design decisions, and project direction were reviewed and managed by the project author.

---

# License

This project is released under the **MIT License**.

You are free to:

- Use
- Modify
- Distribute
- Fork

Please keep the original license file with any redistribution.

---

# Acknowledgements

Special thanks to:

- HandBrake developers
- HandBrakeCLI project
- Qt / PySide6
- Python community

Without HandBrakeCLI this application would not exist.

---

# Disclaimer

Batch Video Encoder is an independent project and is **not affiliated with or endorsed by the HandBrake project**.

HandBrake® and HandBrakeCLI are trademarks of their respective owners.
