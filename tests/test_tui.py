"""The TUI, headless, against the simulated Pilot: connect, health, quick live + CSV with a marker, Mode 06,
info, a read-only query (and a refused write), log summary. Skipped if textual isn't installed."""

import asyncio
import csv
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["YF_ADAPTER_FILE"] = os.path.join(tempfile.mkdtemp(), ".adapter")  # never the repo's own .adapter

try:
    import textual  # noqa: F401
except ImportError:
    textual = None

if textual:
    from textual.widgets import DataTable, RichLog, Static, Switch

    import tui  # noqa: E402

DEMO = "sim://pilot?latency=0"


def text_of(widget):
    r = widget.render()
    return getattr(r, "plain", str(r))


@unittest.skipIf(textual is None, "textual not installed")
class TestTui(unittest.TestCase):
    def test_full_stack_against_demo(self):
        asyncio.run(self.drive())

    async def wait(self, pilot, cond, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            await pilot.pause(0.05)
            if cond():
                return
        self.fail("timed out waiting for the TUI")

    async def drive(self):
        with tempfile.TemporaryDirectory() as d:
            app = tui.YfTui(DEMO, log_dir=d)
            async with app.run_test(size=(160, 50)) as pilot:
                await pilot.click("#connect-btn")
                await self.wait(pilot, lambda: app.car is not None and app.car.connected)
                await pilot.pause(0.2)
                self.assertEqual(app.query_one("#modules", DataTable).row_count, 2)

                await pilot.press("2")
                await pilot.click("#health-btn")
                out = app.query_one("#healthout", Static)
                await self.wait(pilot, lambda: "Honda reads" in text_of(out))
                h = text_of(out)
                self.assertIn("P3400", h)          # DEMO's stored code
                self.assertIn("INCOMPLETE", h)     # DEMO's EVAP monitor
                self.assertIn("Highest: cylinder 3", h)

                await pilot.press("3")
                app.query_one("#quick", Switch).value = True
                await pilot.click("#live-btn")
                await self.wait(pilot, lambda: app.live_running and len(app.rate) > 2)
                await pilot.click("#rec-btn")
                await self.wait(pilot, lambda: app.sink.csv is not None and app.sink.csv.rows >= 2)
                await pilot.press("m")
                await self.wait(pilot, lambda: app.sink.csv.rows >= 5)
                await pilot.click("#rec-btn")
                await pilot.click("#live-btn")
                await self.wait(pilot, lambda: not app.live_running)
                logs = [f for f in os.listdir(d) if f.endswith(".csv")]
                self.assertEqual(len(logs), 1)
                with open(os.path.join(d, logs[0]), encoding="utf-8") as f:
                    rows = list(csv.DictReader(ln for ln in f if not ln.startswith("#")))
                    f.seek(0)
                    self.assertIn("# front end: tui.py", f.read())
                self.assertIn("mark1", [r["marker"] for r in rows])
                data = [r for r in rows if not r["marker"].startswith(("timeout", "link"))]
                self.assertTrue(all(r["rpm"] for r in data))
                self.assertTrue(any(not r["coolant F"] for r in data))  # quick: coolant only every 10 s
                self.assertTrue(str(app.query_one("#livetable", DataTable).get_cell("COOLANT_TEMP", "now")))

                await pilot.press("4")
                await pilot.click("#mode06-btn")
                await self.wait(pilot, lambda: app.query_one("#mode06table", DataTable).row_count > 0)

                await pilot.press("5")
                await pilot.click("#info-btn")
                await self.wait(pilot, lambda: "VIN" in text_of(app.query_one("#infoout", Static)))

                await pilot.press("6")
                app.query_one("#query").value = "222201 1D"
                await pilot.click("#query-btn")
                ql = app.query_one("#queryout", RichLog)
                await self.wait(pilot, lambda: any("bytes:" in str(ln.text) for ln in ql.lines))
                app.query_one("#query").value = "04"
                await pilot.click("#query-btn")
                await self.wait(pilot, lambda: any("refused" in str(ln.text) for ln in ql.lines))

                await pilot.press("7")
                await pilot.click("#logs-btn")
                lt = app.query_one("#loglist", DataTable)
                self.assertEqual(lt.row_count, 1)
                app.summarize(os.path.join(d, logs[0]))
                await self.wait(pilot, lambda: "Markers:" in text_of(app.query_one("#summaryout", Static)))


if __name__ == "__main__":
    unittest.main()
