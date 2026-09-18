"""The browser body: browser-use/jev-ultrafast (MIT) driving Chrome over CDP.

Inside a page, a DOM snapshot beats the accessibility tree — the page owns its state machine and
rejects anything that isn't a real DOM event, which is why AT-SPI + synthetic input loses to a
site like Google Flights. So browser errands go here and everything else stays on AT-SPI.

Two things are patched at import, both one-liners upstream, so nothing is forked:
  * decisions go to the OpenRouter Decisions router instead of api.typesafe.ai (that is the Jev
    access this machine has — see hearthsmith.decide),
  * the tab is opened in the foreground, because the assistant works where its user can watch.
"""
