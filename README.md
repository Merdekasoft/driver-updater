Driver Updater - Version 2.0
Driver Updater is a graphical user interface (GUI) utility designed to help Debian/Ubuntu users manage their system drivers. The application scans for available driver updates and provides an easy way to install them, ensuring your hardware runs optimally.

Key Features
Smart Scanning: Utilizes the ubuntu-drivers-common API (detect.py) for accurate driver identification, with a fallback method using apt if the API is not available.

Responsive Interface: The entire scanning and updating process runs in a separate thread, keeping the UI responsive. You can even cancel an ongoing scan.

Clear Display: Scan results show packages, current versions, and available versions in an easy-to-read format.

One-Click Updates: The "Update All" feature allows you to update all found drivers with a single click.

Background Management: The application can be minimized to the system tray, allowing you to continue using it without closing the window.

System Requirements
Debian or Ubuntu-based operating system.

Python 3.

apt and pkexec (for administrative privileges).

Installation
1. Clone the Repository
git clone [https://github.com/merdekasoft/driver-updater.git](https://github.com/merdekasoft/driver-updater.git)
cd driver-updater

2. Install Dependencies
The application is built using PySide6. Install it with pip:

pip install pyside6

Note: Depending on your system, you may already have ubuntu-drivers-common installed. If not, the application will use the apt scanning method as a fallback.

3. Run the Application
Execute the application from the terminal:

python3 driver-updater.py

Usage
Open the Application: Run driver-updater.py. You will see the scanning page.

Start Scan: Click the SCAN button to check for drivers that need updating.

View Results: Once the scan is complete, the app will switch to the results page, showing a list of drivers that can be updated or are recommended.

Perform Updates:

Click the Update button next to a specific driver to update it individually.

Click the Update All button to update all drivers on the list.

Reboot (Optional): If the update involves the kernel or other core drivers, a message will appear recommending that you reboot your computer to fully apply the changes.

Contributing
Contributions are very welcome! If you find a bug or have an idea for an improvement, please open an issue or submit a pull request on the GitHub repository.

License
This project is licensed under the MIT License. See the LICENSE file for details.
