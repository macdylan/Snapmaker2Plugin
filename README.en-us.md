[Please click here for Chinese README.md](README.md)
# Snapmaker 2 Plugin for Cura
- Automatically find all Snapmaker 2 devices in your LAN via UDP broadcast.
- Send files directly to the printer over wifi, start printing from touchscreen.
- Model thumbnails are displayed on touchscreen, optimized gcode for printing on Snapmaker 2.

# Installation
## Pre-requisites
Install the 3 provided profile files from [snapmaker official cura profiles page](https://support.snapmaker.com/hc/en-us/articles/360044341034) (they are at bottom of page).

## From Cura Marketplace
1. Click the Marketplace button in upper right of main screen
2. Scroll though community Plgins and click Snapmaker2Plugin icon
3. Click Install
4. Agree to Gnu General Public License
5. Quit and restart Cura

## From github
1. Open the Show Configuration Folder from cura's Help menu and go to the plugins folder
2. It is recommended to clone this repo locally into this location using the command line `git clone https://github.com/macdylan/Snapmaker2Plugin.git` or
3. Download the zip package for [release](https://github.com/macdylan/Snapmaker2Plugin/releases) and unzip to the folder shown in step 1 (you shgould now have a folder called `Snapmaker2Plugin` in the `plugins` directory)
4. Quit and restart Cura

# Usage
- Click blue slice button in the workspace.
- After slicing a model, the save file / device selection menu appears at the lower right of the worksapce

    ![](_snapshots/sendto.png)
- Select the device you want to send and click Send to
- If this is the firstime you have used this plugin you will be prompted to tap yes on the Snapmaker touchscreen

    ![](_snapshots/screen_auth.png)

- Tap Yes in Snapmaker 2 touchscreen WiFi Connection Request (only required the first time after boot)
- Tap disconnect on the touchscreen or wait for touchscreen to return to home
- Tap start buton on touchscreen
- Navigate to files and choose the file that was just uploaded to your snapmaker

  ![](_snapshots/preview.jpg)

- Tap start (or adjust settings as needed)

Once printing has started you can close Cura as all progress is monitored on the Snapmakers touchscreen


- Go back to Cura and click the Continue button and wait for the file to be sent
- Confirm printing on the Snapmaker 2 touchscreen
- You can also use Save to save files in Snapmaker G-code file format to disk if required

    ![](_snapshots/savetofile.png)

## Troubleshooting Snapmaker Discovery
⚠️ If your snapmaker name or IP address does not appear try the following steps:
   1. Ensure you snapmaker is connected to wifi by checking your wireless router or checking on the Snapmakers touchscreen 
   2. Wait 5-10 seconds, Cura continuously looks for all compatible devices in the LAN and displays them automatically
   3. Restart Snapmaker 2 and wait for touchscreen to fully start and ensure it is connected to the right wireless lan
   4. Check your computer's firewall to see if Cura access to the local area network is blocked (win10 is blocked by default)
   5. Check the router settings to see if UDP broadcasting is blocked
   6. If possible, make sure that your computer and the  Snapmaker 2 have good wifi reception with your router to eliminate the chance of network issues.

    Please note that the instructions above are valid on firmware 1.12.1 and 1.12.3; touchscreen UI flows do vary by version; also as of writing wifi network connecitvity of the A350 can be unreliable.


---
<sup>Make Something Wonderful</sup>
