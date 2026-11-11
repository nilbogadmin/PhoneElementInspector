# Phone Element Inspector

Windows app that shows the top half of one Android phone and clicks saved elements on every phone connected over ADB.

## Download

On this page choose Code, then Download ZIP. Unzip it and double-click `RUN_WINDOWS.bat`.

That script makes a Python virtual environment, installs the packages, and starts the app. You need Python 3.10 or newer on PATH, plus ADB with USB debugging on the phones.

## Use

The mirror stays open. The dropdown at the top picks which phone you train on. ADD names the selected element and creates a click-if-visible rule. START runs those rules on every connected phone.
