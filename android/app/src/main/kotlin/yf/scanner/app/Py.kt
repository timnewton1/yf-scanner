package yf.scanner.app

import com.chaquo.python.PyObject
import com.chaquo.python.Python
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.UUID

/*
 * The scanner is the repo's Python (scanner/, summarize.py), embedded with Chaquopy: scanner/phone.py
 * is its door. Kotlin here is only what needs Android: this bridge, the BLE link, the screens.
 * Calls go through Scanner's one worker thread (one request on the adapter at a time); answers
 * come back as JSON.
 */

/** A byte pipe to the adapter, for scanner/phone.py's LinkPort. BleLink is the only one: DEMO runs in Python. */
interface Link {
    /** Send bytes. Throws LinkException if the link is gone. */
    fun write(data: ByteArray)

    /** Whatever has arrived, waiting up to timeoutMs for the first byte. Empty on timeout. Throws if the link dropped. */
    fun read(timeoutMs: Long): ByteArray

    fun resetInput()
    fun close()

    /** Lines describing the link (device, channel, MTU, services), for the Adapter card. */
    fun info(): List<String> = emptyList()
}

class LinkException(msg: String, cause: Throwable? = null) : IOException(msg, cause)

/** What scanner/phone.py calls back on (its docstring lists them). Called on the worker thread. */
interface PyHooks {
    fun openLink(address: String): Link
    fun traffic(direction: String, text: String)
    fun section(json: String)
    fun row(json: String)
    fun status(text: String)
    fun stopRequested(): Boolean
    fun takeMarker(): String
}

// --- what the screens show: scanner/report.py's sections ---------------------------------

enum class Tone { NORMAL, GOOD, WARN, BAD, DIM }

data class Line(val text: String, val tone: Tone = Tone.NORMAL, val indent: Int = 0)

data class Section(val title: String, val lines: List<Line>) {
    companion object {
        fun of(o: JSONObject): Section {
            val ls = o.getJSONArray("lines")
            return Section(o.getString("title"), List(ls.length()) { i ->
                val l = ls.getJSONObject(i)
                Line(l.getString("text"), tone(l.optString("tone")), l.optInt("indent"))
            })
        }

        fun of(json: String) = of(JSONObject(json))

        private fun tone(t: String) = when (t) {
            "good" -> Tone.GOOD
            "warn" -> Tone.WARN
            "bad" -> Tone.BAD
            "dim" -> Tone.DIM
            else -> Tone.NORMAL
        }
    }
}

fun JSONArray.strings(): List<String> = List(length()) { getString(it) }

data class Profile(val label: String, val service: UUID, val notify: UUID, val write: UUID)

object Py {
    private val mod: PyObject by lazy { Python.getInstance().getModule("scanner.phone") }

    /** Call a scanner/phone.py function; its JSON (or text) answer. */
    fun call(name: String, vararg args: Any?): String = mod.callAttr(name, *args).toString()

    /** Log sets, and how ble.py recognizes a BLE OBD adapter (BleLink and the scan use the same lists). */
    private val hello: JSONObject by lazy { JSONObject(call("hello")) }

    val sets: List<String> by lazy { hello.getJSONObject("sets").keys().asSequence().toList() }

    val profiles: List<Profile> by lazy {
        val a = hello.getJSONArray("profiles")
        List(a.length()) { i ->
            val p = a.getJSONArray(i)
            Profile(p.getString(0), UUID.fromString(p.getString(1)), UUID.fromString(p.getString(2)), UUID.fromString(p.getString(3)))
        }
    }

    val skipServices: Set<UUID> by lazy { hello.getJSONArray("skip").strings().map(UUID::fromString).toSet() }

    val likelyName: Regex by lazy { Regex(hello.getString("likely_name"), RegexOption.IGNORE_CASE) }

    val pythonVersion: String get() = hello.getString("python")
}
