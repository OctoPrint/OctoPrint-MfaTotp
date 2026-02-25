# OctoPrint-MfaTotp

A plugin to support TOTP based Two Factor Authentication in OctoPrint >= 1.11.0.

![Screenshot of the login workflow, showing an additional prompt added to the login dialog, asking for entering a second factor, with TOTP being an option.](https://raw.githubusercontent.com/OctoPrint/OctoPrint-MfaTotp/main/extras/screenshot_login.png)

Successfully tested with

  - Google Authenticator
  - Aegis
  - 1Password (community)

but adheres to the TOTP standard and should work with any related apps.

To enroll your user account, open the User Settings, then under 2FA: TOTP click on Enroll and follow the instructions.

![Screenshot of the enrollment dialog, showing a QR Code to scan with an authenticator app and asking for a first token to be entered to confirm enrollment.](https://raw.githubusercontent.com/OctoPrint/OctoPrint-MfaTotp/main/extras/screenshot_enrollment.png)

## Setup

Install via the bundled [Plugin Manager](https://docs.octoprint.org/en/master/bundledplugins/pluginmanager.html)
or manually using this URL:

    https://github.com/OctoPrint/OctoPrint-MfaTotp/archive/main.zip
