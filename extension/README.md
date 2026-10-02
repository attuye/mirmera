# Mirmera recorder (Chrome extension)

Records an exploratory testing session from your own Chrome into a running
Mirmera session: an alternative to the recorded browser when you need your
usual logins, extensions or devices.

It records **one tab**, from **Record this tab** until **Stop recording**
(or the tab closes), and sends:

- your actions (clicks, typing, selects, checkboxes, Enter, uploads), with the
  same recorder as the recorded browser, so regression tests and the
  reproduction agent can use them;
- console errors and uncaught page errors;
- failed requests (4xx/5xx, or no response) and the pages you visit;
- screenshots (**Screenshot**, and automatically when you log a bug);
- notes and bugs typed in the popup.

Privacy: passwords and other secret fields are never recorded, and Mirmera
removes the values from URL query strings (`?token=…`), because your everyday
browser carries real sessions. The backend applies these rules again. The
toolbar badge shows **REC** while a tab is being recorded. Nothing is sent
anywhere but the Mirmera API you set.

## Install (developer mode)

1. Open `chrome://extensions`, turn on **Developer mode**.
2. **Load unpacked**, choose this `extension` folder.
3. Pin **Mirmera recorder** to the toolbar.

## Use

1. Start a session in Mirmera (Dashboard, click a charter).
2. Open the app you are testing in a tab, click the extension, pick the
   session, **Record this tab**.
3. Test. Log notes and bugs from the popup or from Mirmera's session screen;
   the session screen shows what the extension sends.
4. **Stop recording** when done, then end the session in Mirmera as usual.

Settings: the Mirmera API address (default `http://localhost:8000`) and app
address (default `http://localhost:5173`).

`recorder.js` is a copy of `backend/app/recorder.js`; a backend test fails if
they differ. After changing the recorder, copy it here.
