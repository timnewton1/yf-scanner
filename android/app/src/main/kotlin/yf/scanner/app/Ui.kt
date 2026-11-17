package yf.scanner.app

import android.content.Intent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import java.io.File

fun Modifier.pad() = padding(horizontal = 12.dp, vertical = 4.dp)

// --- building blocks -------------------------------------------------------------------

@Composable
fun Page(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp), content = content)
}

@Composable
fun toneColor(t: Tone): Color = when (t) {
    Tone.GOOD -> Color(0xFF2E7D32)
    Tone.WARN -> Color(0xFFE08A00)
    Tone.BAD -> MaterialTheme.colorScheme.error
    Tone.DIM -> MaterialTheme.colorScheme.onSurfaceVariant
    Tone.NORMAL -> MaterialTheme.colorScheme.onSurface
}

@Composable
fun SectionCard(s: Section, mono: Boolean = false) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(s.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
            SelectionContainer {
                // mono: tables (Mode 06, PIDs, raw bytes) keep their columns and scroll sideways
                Column(if (mono) Modifier.horizontalScroll(rememberScrollState()) else Modifier, verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    for (l in s.lines) {
                        Text(l.text, color = toneColor(l.tone), modifier = Modifier.padding(start = (l.indent * 14).dp),
                            style = if (mono) MaterialTheme.typography.bodySmall else MaterialTheme.typography.bodyMedium,
                            softWrap = !mono,
                            fontFamily = if (mono) FontFamily.Monospace else FontFamily.Default,
                            fontWeight = if (l.tone == Tone.BAD) FontWeight.SemiBold else FontWeight.Normal)
                    }
                }
            }
        }
    }
}

@Composable
fun Note(text: String) = Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

@Composable
fun Results(key: String, mono: Boolean = false) {
    val results by Scanner.results.collectAsState()
    results[key]?.forEach { SectionCard(it, mono) }
}

@Composable
fun NeedsCar(content: @Composable ColumnScope.() -> Unit) {
    val conn by Scanner.conn.collectAsState()
    Page {
        if (!conn.connected) Note("Not connected to the car. Connect first on the Connect tab (or try the simulated Pilot there).")
        content()
    }
}

@Composable
fun Buttons(vararg b: Pair<String, () -> Unit>, enabled: Boolean = true) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        b.forEachIndexed { i, (t, f) ->
            if (i == 0) Button(onClick = f, enabled = enabled) { Text(t) } else OutlinedButton(onClick = f, enabled = enabled) { Text(t) }
        }
    }
}

// --- 1 Connect -------------------------------------------------------------------------

@Composable
fun ConnectTab() {
    val conn by Scanner.conn.collectAsState()
    val found by Scanner.found.collectAsState()
    val scanning by Scanner.scanning.collectAsState()
    val busy by Scanner.busy.collectAsState()
    val withBt = rememberWithBluetooth()
    val saved = Scanner.saved
    Page {
        Card(Modifier.fillMaxWidth(), colors = if (conn.error) CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer) else CardDefaults.cardColors()) {
            SelectionContainer { Text(conn.message, Modifier.padding(12.dp)) }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { withBt { Scanner.connect(null) } }, enabled = busy == null) {
                Text(if (saved != null) "Connect" else "Find & connect")
            }
            OutlinedButton(onClick = { Scanner.connect("DEMO") }, enabled = busy == null) { Text("Demo") }
            if (conn.open) OutlinedButton(onClick = { Scanner.disconnect() }) { Text("Disconnect") }
        }
        Note(if (saved != null) "Saved adapter: $saved. Connect goes straight to it." else
            "Connect looks for the one BLE OBD adapter in range and remembers it. Demo is a simulated Pilot (one stored code, a misfiring cylinder 3), to try the app without the car.")
        Note("Adapter in the OBD port under the dash, driver's side; key ON (engine off is fine). Close any other phone app using the adapter first: it takes one connection at a time.")

        HorizontalDivider()
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { withBt { Scanner.scan() } }, enabled = !scanning) { Text(if (scanning) "Scanning..." else "Scan for adapters") }
            if (saved != null) TextButton(onClick = { Scanner.forget() }) { Text("Forget saved") }
        }
        for (d in found) {
            Card(Modifier.fillMaxWidth().clickable(enabled = busy == null) { withBt { Scanner.connect(d.address) } }) {
                Column(Modifier.padding(10.dp)) {
                    Text(d.name.ifEmpty { "(no name)" } + if (d.likely) "  · likely OBD" else "", fontWeight = if (d.likely) FontWeight.SemiBold else FontWeight.Normal)
                    Note("${d.address}  rssi ${d.rssi}" + if (d.why.isNotEmpty()) "  (${d.why})" else "")
                }
            }
        }
        if (found.isNotEmpty()) Note("Tap one to connect. The reference adapter advertises as OBDII.")

        conn.adapter?.let {
            HorizontalDivider()
            SectionCard(it)
        }
        Note("Reading is safe. Nothing in this app commands the car; the only thing that changes anything is Clear codes on the Health tab, which asks first.")
    }
}

// --- 2 Health --------------------------------------------------------------------------

@Composable
fun HealthTab() {
    val busy by Scanner.busy.collectAsState()
    var confirm by remember { mutableStateOf(false) }
    NeedsCar {
        Buttons("Health check" to { Scanner.run("health", "Health check: codes, readiness, misfires, Mode 06, live, Honda...", "health") },
            "Codes only" to { Scanner.run("health", "Reading codes...", "codes") },
            enabled = busy == null)
        Note("Key ON (engine off is fine). Codes from both modules, readiness, misfire counters, Mode 06 results outside their limits, a live snapshot and the Honda candidate reads.")
        Results("health")
        HorizontalDivider()
        OutlinedButton(onClick = { confirm = true }, enabled = busy == null,
            colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Clear codes...") }
    }
    if (confirm) ClearDialog(onDismiss = { confirm = false }) { confirm = false; Scanner.clearCodes() }
}

@Composable
fun ClearDialog(onDismiss: () -> Unit, onClear: () -> Unit) {
    var typed by remember { mutableStateOf("") }
    AlertDialog(onDismissRequest = onDismiss,
        title = { Text("Clear codes?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Clearing codes also resets every readiness monitor and the learned fuel trims.")
                Text("The car will FAIL an emissions test until the monitors complete again (days of driving).", fontWeight = FontWeight.SemiBold)
                Text("Permanent codes stay until the car confirms the fix itself. Key ON, engine OFF.")
                OutlinedTextField(typed, { typed = it }, label = { Text("Type clear to go ahead") }, singleLine = true)
            }
        },
        confirmButton = { TextButton(onClick = onClear, enabled = typed.trim().lowercase() == "clear") { Text("Clear") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}

// --- 3 Live ----------------------------------------------------------------------------

@Composable
fun LiveTab() {
    val log by Scanner.log.collectAsState()
    val conn by Scanner.conn.collectAsState()
    var set by rememberSaveable { mutableStateOf("drive") }
    var honda by rememberSaveable { mutableStateOf(true) }
    var tag by rememberSaveable { mutableStateOf("") }
    var word by rememberSaveable { mutableStateOf("") }
    val withBt = rememberWithBluetooth()
    Page {
        if (!log.running) {
            if (!conn.connected) Note("Not connected to the car. Connect first on the Connect tab.")
            Text("Set", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                for (s in Py.sets) FilterChip(selected = set == s, onClick = { set = s }, label = { Text(s) })
            }
            Note(when (set) {
                "drive" -> "18 values, about 2 rows/s: baseline and longevity logs."
                "noise" -> "6 values in one request, about 5 rows/s: for chasing a noise."
                else -> "Everything the engine computer answers. Slow."
            })
            Row(verticalAlignment = Alignment.CenterVertically) {
                Switch(honda, { honda = it })
                Spacer(Modifier.width(8.dp))
                Text("Honda reads: ATF temp, VCM cylinders (candidate decodes)")
            }
            OutlinedTextField(tag, { tag = it }, label = { Text("Tag for the file name (baseline, noise, after-plugs)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Button(onClick = { withBtIfReal(conn.target, withBt) { Scanner.startLogging(set, honda, tag) } }, enabled = conn.connected,
                modifier = Modifier.fillMaxWidth().height(56.dp)) { Text("Start logging") }
            Note("Engine running. Logs go to the Logs tab as CSV (the same columns as scan.py, so summarize.py reads them too). The screen stays on while logging; the log keeps going if it turns off. Don't work the phone while driving: let a passenger tap the marker.")
            if (log.status.isNotEmpty()) SelectionContainer { Note(log.status) }
            return@Page
        }

        // running
        Button(onClick = { Scanner.mark(word.ifBlank { null }); word = "" }, modifier = Modifier.fillMaxWidth().height(96.dp),
            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.tertiary)) {
            Text("MARK", fontSize = 28.sp, fontWeight = FontWeight.Bold)
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for (w in listOf("whine", "drone", "clunk", "rattle", "hum", "shudder")) OutlinedButton(onClick = { Scanner.mark(w) }) { Text(w) }
        }
        OutlinedTextField(word, { word = it }, label = { Text("Word for the next MARK (optional)") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.None))
        if (log.lastMarker.isNotEmpty()) Note("Last marker: ${log.lastMarker}")

        log.tiles.chunked(3).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { (n, label) -> Tile(label, log.cells[n] ?: "-", Modifier.weight(1f)) }
                repeat(3 - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
        Note("%d rows, %.0f s (%.2f rows/s) → %s".format(log.rows, log.seconds, log.rows / maxOf(log.seconds, 0.001), log.file?.name ?: ""))
        if (log.status.isNotEmpty()) Note(log.status)
        if (log.skipped.isNotEmpty()) Note("Not answered by this engine computer, skipped: ${log.skipped.joinToString(", ")}")
        OutlinedButton(onClick = { Scanner.stopLogging() }, modifier = Modifier.fillMaxWidth()) { Text("Stop logging") }
    }
}

private fun withBtIfReal(target: String?, withBt: ((() -> Unit) -> Unit), f: () -> Unit) =
    if (target == "DEMO") f() else withBt(f)

@Composable
fun Tile(name: String, value: String, modifier: Modifier) {
    Card(modifier) {
        Column(Modifier.padding(8.dp)) {
            Text(name, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            Text(value, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold, maxLines = 1)
        }
    }
}

// --- 4 Mode 06 -------------------------------------------------------------------------

@Composable
fun Mode06Tab() {
    val busy by Scanner.busy.collectAsState()
    NeedsCar {
        Buttons("All results" to { Scanner.run("mode06", "Reading every Mode 06 result...", "mode06") },
            "Failed only" to { Scanner.run("mode06", "Reading Mode 06...", "mode06_failed") },
            "Misfires" to { Scanner.run("mode06", "Reading misfire counters...", "misfires") },
            enabled = busy == null)
        Note("On-board test results with the limits the engine computer judges them by. Test IDs 80 and up are Honda's own and unpublished, so they're named by number.")
        Results("mode06", mono = true)
    }
}

// --- 5 Info ----------------------------------------------------------------------------

@Composable
fun InfoTab() {
    val busy by Scanner.busy.collectAsState()
    NeedsCar {
        Buttons("Vehicle" to { Scanner.run("info", "Reading VIN, module names, calibration IDs...", "vehicle") },
            "Supported" to { Scanner.run("info", "Reading supported PIDs...", "supported") },
            "Honda probe" to { Scanner.run("info", "Honda Mode 22 reads...", "honda_probe") },
            enabled = busy == null)
        Note("The VIN is shown here only; the app doesn't send it anywhere.")
        Results("info", mono = true)
    }
}

// --- 6 Console -------------------------------------------------------------------------

@Composable
fun ConsoleTab() {
    val busy by Scanner.busy.collectAsState()
    val traffic by Scanner.traffic.collectAsState()
    var req by rememberSaveable { mutableStateOf("0902") }
    var module by rememberSaveable { mutableStateOf("engine") }
    var record by remember { mutableStateOf(Scanner.recordTraffic) }
    NeedsCar {
        Note("One read-only request to one module. Services allowed: 01 02 03 06 07 09 0A 22. Anything else is refused.")
        OutlinedTextField(req, { req = it.uppercase() }, label = { Text("Request (hex), e.g. 0101, 0902, 222201") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for (m in listOf("engine", "transmission")) FilterChip(selected = module == m, onClick = { module = m }, label = { Text(m) })
            OutlinedTextField(if (module in listOf("engine", "transmission")) "" else module, { module = it.uppercase().ifEmpty { "engine" } },
                label = { Text("or hex") }, singleLine = true, modifier = Modifier.width(110.dp))
        }
        Button(onClick = { Scanner.run("console", "Sending $req...", "query", req, module) }, enabled = busy == null) { Text("Send") }
        Results("console", mono = true)
        HorizontalDivider()
        Row(verticalAlignment = Alignment.CenterVertically) {
            Switch(record, { record = it; Scanner.recordTraffic = it })
            Spacer(Modifier.width(8.dp))
            Text("Record adapter traffic (every line sent and received)")
        }
        if (traffic.isNotEmpty()) {
            TextButton(onClick = { Scanner.clearTraffic() }) { Text("Clear") }
            SelectionContainer {
                Text(traffic.takeLast(150).joinToString("\n"), fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                    modifier = Modifier.horizontalScroll(rememberScrollState()))
            }
        }
    }
}

// --- 7 Logs ----------------------------------------------------------------------------

@Composable
fun LogsTab() {
    val ctx = LocalContext.current
    val log by Scanner.log.collectAsState()
    var files by remember { mutableStateOf(Scanner.logFiles()) }
    var open by remember { mutableStateOf<File?>(null) }
    var summary by remember { mutableStateOf("") }
    var del by remember { mutableStateOf<File?>(null) }
    LaunchedEffect(log.running, log.rows / 50) { files = Scanner.logFiles() }
    LaunchedEffect(open) {
        summary = open?.let { f -> kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            Scanner.summary(f)
        } } ?: ""
    }
    fun share(f: File) {
        val uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", f)
        ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/csv").putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), f.name))
    }
    fun shareText(name: String, text: String) {
        ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_SUBJECT, name)
            .putExtra(Intent.EXTRA_TEXT, text), name))
    }
    Page {
        val f = open
        if (f != null) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { open = null }) { Text("Back") }
                Button(onClick = { share(f) }) { Text("Share CSV") }
                OutlinedButton(onClick = { shareText(f.name, summary) }, enabled = summary.isNotEmpty()) { Text("Share summary") }
            }
            if (summary.isEmpty()) Note("Summarizing...")
            Box(Modifier.horizontalScroll(rememberScrollState())) {
                SelectionContainer { Text(summary, fontFamily = FontFamily.Monospace, fontSize = 11.sp, lineHeight = 14.sp) }
            }
            Note("Trim thresholds are rules of thumb, not Honda specs. Values marked (candidate) are unconfirmed decodes.")
            return@Page
        }
        Note("Drive logs on this phone, newest first. Tap one for its summary: timeline, warm-up, charging, trims by condition, VCM, every marker. Share sends the CSV to a laptop or a chat with your agent.")
        if (files.isEmpty()) Note("No logs yet. Start one on the Live tab.")
        for (x in files) {
            Card(Modifier.fillMaxWidth().clickable { open = x }) {
                Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(x.name, fontWeight = FontWeight.Medium)
                        Note("%.1f kB".format(x.length() / 1024.0) + if (log.running && log.file == x) "  · recording" else "")
                    }
                    if (!(log.running && log.file == x)) TextButton(onClick = { del = x }) { Text("Delete") }
                }
            }
        }
        Note("Folder: ${Scanner.logsDir.absolutePath}")
    }
    del?.let { d ->
        AlertDialog(onDismissRequest = { del = null }, title = { Text("Delete ${d.name}?") },
            text = { Text("The log is deleted from this phone. Share it first if you want to keep it.") },
            confirmButton = { TextButton(onClick = { d.delete(); del = null; files = Scanner.logFiles() }) { Text("Delete") } },
            dismissButton = { TextButton(onClick = { del = null }) { Text("Cancel") } })
    }
}
