package yf.scanner.app

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.ParcelUuid
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.Executors

/*
 * Everything the screens show, and the one worker thread that calls into the Python scanner (one
 * request on the adapter at a time, as tui.py's lock does). Lives as long as the app process, so a
 * drive log keeps going when the screen rotates or turns off (LogService keeps the process alive).
 */

data class Found(val address: String, val name: String, val rssi: Int, val likely: Boolean, val why: String)

data class Conn(
    val open: Boolean = false,      // a session is open (the adapter answered)
    val connected: Boolean = false, // ...and the car answered
    val target: String? = null,
    val message: String = "Not connected.",
    val error: Boolean = false,
    val adapter: Section? = null,
)

data class LogState(
    val running: Boolean = false,
    val file: File? = null,
    val rows: Int = 0,
    val seconds: Double = 0.0,
    val cells: Map<String, String> = emptyMap(),
    val tiles: List<Pair<String, String>> = emptyList(),  // (name, label)
    val lastMarker: String = "",
    val status: String = "",
    val skipped: List<String> = emptyList(),
)

object Scanner {
    lateinit var ctx: Context
        private set
    private val worker = Executors.newSingleThreadExecutor { Thread(it, "adapter") }.asCoroutineDispatcher()
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    val conn = MutableStateFlow(Conn())
    val busy = MutableStateFlow<String?>(null)
    val found = MutableStateFlow<List<Found>>(emptyList())
    val scanning = MutableStateFlow(false)
    val results = MutableStateFlow<Map<String, List<Section>>>(emptyMap())
    val log = MutableStateFlow(LogState())
    val traffic = MutableStateFlow<List<String>>(emptyList())
    @Volatile var recordTraffic = false
    val toast = MutableStateFlow<String?>(null)

    private val marks = ConcurrentLinkedQueue<String>()
    private var markCount = 0
    @Volatile private var stopLog = false
    @Volatile private var resultKey = ""

    fun init(c: Context) { ctx = c.applicationContext }

    private val prefs get() = ctx.getSharedPreferences("yf", Context.MODE_PRIVATE)

    /** The adapter that last answered (a BLE address), like .adapter on the laptop. */
    var saved: String?
        get() = prefs.getString("adapter", null)
        set(v) { prefs.edit().apply { if (v == null) remove("adapter") else putString("adapter", v) }.apply() }

    val logsDir: File get() = File(ctx.getExternalFilesDir(null) ?: ctx.filesDir, "logs").apply { mkdirs() }

    fun logFiles(): List<File> = logsDir.listFiles { f -> f.name.endsWith(".csv") }?.sortedByDescending { it.lastModified() } ?: emptyList()

    // --- what Python calls back on ---------------------------------------------------------

    private val ring = ArrayDeque<String>()
    private var lastPush = 0L

    private fun addSection(key: String, s: Section) { results.value = results.value + (key to (results.value[key].orEmpty() + s)) }

    private val hooks = object : PyHooks {
        override fun openLink(address: String): Link = BleLink(ctx, address).open()

        override fun traffic(direction: String, text: String) {
            if (recordTraffic) synchronized(ring) {
                ring.addLast("$direction $text")
                while (ring.size > 300) ring.removeFirst()
                val now = System.currentTimeMillis()
                if (now - lastPush > 200) { lastPush = now; this@Scanner.traffic.value = ring.toList() }
            }
        }

        override fun section(json: String) = addSection(resultKey, Section.of(json))

        override fun row(json: String) {
            val o = JSONObject(json)
            val c = o.getJSONObject("cells")
            val marker = o.getString("marker")
            val s = log.value
            val rows = o.getInt("rows")
            val secs = o.getDouble("seconds")
            log.value = s.copy(rows = rows, seconds = secs, cells = c.keys().asSequence().associateWith { c.getString(it) },
                lastMarker = if (marker.isNotEmpty()) "'$marker' at %.0f s".format(secs) else s.lastMarker)
            if (rows % 20 == 0) LogService.update(ctx, "$rows rows, ${s.file?.name}")
        }

        override fun status(text: String) { log.value = log.value.copy(status = text) }

        override fun stopRequested() = stopLog

        override fun takeMarker(): String = generateSequence { marks.poll() }.joinToString(";")
    }

    fun flushTraffic() = synchronized(ring) { traffic.value = ring.toList() }
    fun clearTraffic() = synchronized(ring) { ring.clear(); traffic.value = emptyList() }

    // --- connecting --------------------------------------------------------------------

    private fun open(target: String): JSONObject = JSONObject(Py.call("connect", target, hooks))

    /** Connect to an address, "DEMO", or (null) the saved adapter, else the one BLE adapter in range. */
    fun connect(target: String?) {
        if (busy.value != null || log.value.running) return
        scope.launch {
            busy.value = "Connecting..."
            try {
                var t = target ?: saved
                var r: JSONObject? = null
                if (t != null) {
                    busy.value = if (t == "DEMO") "Starting the simulated Pilot..." else "Connecting to $t..."
                    r = withContext(worker) { open(t!!) }
                }
                if (r?.getBoolean("ok") != true && target == null) {
                    if (t != null) toast.value = "Saved adapter didn't answer; looking for others..."
                    busy.value = "Scanning for BLE adapters (6 s)..."
                    val likely = bleScan().filter { it.likely }
                    when (likely.size) {
                        0 -> r = JSONObject().put("ok", false).put("message", "No BLE OBD adapter seen. Plugged in and lit? No other app connected to it? Tap Scan to list everything in range.")
                        1 -> {
                            val address = likely[0].address
                            t = address
                            busy.value = "Connecting to ${likely[0].name.ifEmpty { address }}..."
                            r = withContext(worker) { open(address) }
                        }
                        else -> r = JSONObject().put("ok", false).put("message", "Several BLE adapters in range; pick one in the list below (it's remembered after that).")
                    }
                }
                if (r == null || !r.getBoolean("ok")) {
                    conn.value = Conn(message = r?.getString("message") ?: "No adapter", error = true)
                } else {
                    if (t != "DEMO") saved = t
                    val car = r.getBoolean("car")
                    val text = Section.of(r.getJSONObject("connection")).lines.joinToString("\n") { it.text }
                    conn.value = Conn(true, car, t, text, error = !car, adapter = Section.of(r.getJSONObject("adapter")))
                    results.value = emptyMap()
                }
            } catch (e: Exception) {
                conn.value = Conn(message = "No adapter: ${e.message}", error = true)
            } finally {
                busy.value = null
            }
        }
    }

    fun disconnect() {
        if (log.value.running) stopLogging()
        scope.launch {
            withContext(worker) { try { Py.call("disconnect") } catch (_: Exception) {} }
            conn.value = Conn()
        }
    }

    fun forget() { saved = null; toast.value = "Forgot the saved adapter; Connect will scan again." }

    // --- BLE scan ------------------------------------------------------------------------

    @SuppressLint("MissingPermission")
    suspend fun bleScan(seconds: Long = 6): List<Found> = withContext(Dispatchers.Main) {
        val bt = (ctx.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
        if (bt == null || !bt.isEnabled) { toast.value = "Bluetooth is off"; return@withContext emptyList() }
        val scanner = bt.bluetoothLeScanner ?: return@withContext emptyList()
        val map = LinkedHashMap<String, Found>()
        val known = Py.profiles.map { ParcelUuid(it.service) }.toSet()
        val likelyName = Py.likelyName
        fun sorted() = map.values.sortedWith(compareBy<Found>({ !it.likely }, { -it.rssi }))
        val cb = object : ScanCallback() {
            override fun onScanResult(type: Int, r: ScanResult) {
                val name = r.scanRecord?.deviceName ?: try { r.device.name } catch (_: SecurityException) { null } ?: ""
                val why = mutableListOf<String>()
                if (r.scanRecord?.serviceUuids?.any { it in known } == true) why.add("advertises an OBD adapter service")
                if (name.isNotEmpty() && likelyName.containsMatchIn(name)) why.add("name")
                map[r.device.address] = Found(r.device.address, name, r.rssi, why.isNotEmpty(), why.joinToString(", "))
                found.value = sorted()
            }
            override fun onScanFailed(errorCode: Int) { toast.value = "BLE scan failed (code $errorCode)" }
        }
        scanning.value = true
        found.value = emptyList()
        try {
            scanner.startScan(null, ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), cb)
            delay(seconds * 1000)
        } finally {
            try { scanner.stopScan(cb) } catch (_: Exception) {}
            scanning.value = false
        }
        sorted()
    }

    fun scan() {
        if (scanning.value) return
        scope.launch { bleScan() }
    }

    // --- reads ---------------------------------------------------------------------------

    /** One of scanner/phone.py's reads; its sections land in results[key] as each is read. */
    fun run(key: String, label: String, what: String, vararg args: String) {
        if (!conn.value.connected) { toast.value = "Connect to the car first (Connect tab)"; return }
        if (log.value.running) { toast.value = "Stop the drive log first; the adapter is busy logging"; return }
        if (busy.value != null) return
        scope.launch {
            busy.value = label
            results.value = results.value + (key to emptyList())
            try {
                withContext(worker) { resultKey = key; Py.call("run", what, *args) }
            } catch (e: Exception) {
                addSection(key, Section("Error", listOf(Line(e.message ?: e.toString(), Tone.BAD))))
            } finally {
                busy.value = null
                flushTraffic()
            }
        }
    }

    fun clearCodes() = run("health", "Clearing codes...", "clear")

    /** summarize.py's text for one log (any thread: it doesn't touch the adapter). */
    fun summary(f: File): String = Py.call("summary", f.absolutePath)

    // --- drive log -----------------------------------------------------------------------

    fun startLogging(set: String, honda: Boolean, tag: String) {
        val c = conn.value
        if (!c.connected) { toast.value = "Connect to the car first"; return }
        if (log.value.running || busy.value != null) return
        stopLog = false
        marks.clear()
        markCount = 0
        scope.launch {
            val prep = withContext(worker) { JSONObject(Py.call("log_prepare", set, honda, tag, logsDir.absolutePath)) }
            if (prep.has("error")) { toast.value = prep.getString("error"); return@launch }
            val tiles = prep.getJSONArray("tiles").let { a -> List(a.length()) { a.getJSONArray(it).let { p -> p.getString(0) to p.getString(1) } } }
            val cols = prep.getJSONArray("columns").length()
            log.value = LogState(running = true, file = File(prep.getString("path")), tiles = tiles,
                skipped = prep.getJSONArray("skipped").strings(), status = "Logging $cols values")
            LogService.start(ctx, c.target != "DEMO")
            try {
                val done = withContext(worker) { JSONObject(Py.call("log_run")) }
                log.value = log.value.copy(running = false, status = done.getString("status"))
                if (!done.getBoolean("connected")) conn.value = conn.value.copy(connected = false, error = true, message = "Lost the car; reconnect.")
            } catch (e: Exception) {
                log.value = log.value.copy(running = false, status = "error: ${e.message}")
            } finally {
                LogService.stop(ctx)
            }
        }
    }

    fun stopLogging() { stopLog = true }

    /** A marker on the next row: the word, or markN like scan.py's Enter. */
    fun mark(text: String? = null) {
        if (!log.value.running) return
        val m = text ?: "mark${++markCount}"
        marks.add(m)
        log.value = log.value.copy(lastMarker = "'$m' (next row)")
    }
}
