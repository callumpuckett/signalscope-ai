"""Published core cash-market schedules, reviewed 2026-11-13.

Sources: https://www.nyse.com/trade/hours-calendars
https://www.londonstockexchange.com/en/resources/markets-products/business-days
Unscheduled closures are not available from this static calendar.
"""
from datetime import time

US_CLOSED = {
    2026: "01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25",
    2027: "01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24",
    2028: "01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25",
}
UK_CLOSED = {
    2026: "01-01 04-03 04-06 05-04 05-25 08-31 12-25 12-28",
    2027: "01-01 03-26 03-29 05-03 05-31 08-30 12-27 12-28",
    2028: "01-03 04-14 04-17 05-01 05-29 08-28 12-25 12-26",
}
US_EARLY = {2026: "11-27 12-24", 2027: "11-26", 2028: "07-03 11-24"}
UK_EARLY = {2026: "12-24 12-31", 2027: "12-24 12-31", 2028: "12-22 12-29"}


def scheduled_session_open(now, market):
    closed, early = (US_CLOSED, US_EARLY) if market == "US" else (UK_CLOSED, UK_EARLY)
    if now.year not in closed:
        return None
    day = now.strftime("%m-%d")
    if now.weekday() >= 5 or day in closed[now.year].split():
        return False
    opening = time(9, 30) if market == "US" else time(8)
    closing = (time(13) if market == "US" else time(12, 30)) if day in early[now.year].split() else (time(16) if market == "US" else time(16, 30))
    return opening <= now.time().replace(tzinfo=None) < closing
