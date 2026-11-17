package yf.scanner.app

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.Context
import android.os.Build
import java.io.ByteArrayOutputStream
import java.util.UUID
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/*
 * The BLE transport on the phone: the Android twin of scanner/elm/ble.py, and the one part of the
 * scanner that can't be Python (bleak has no Chaquopy backend). A BLE ELM327 carries its serial
 * stream over two GATT characteristics, one it notifies on and one the phone writes to.
 * Which pair: known layouts first (FFF0 = the reference ELM327 v1.5 BLE mini), then any other
 * service with a notify + write pair. If several qualify, a harmless ATI picks the one wired to the
 * chip. The layouts are ble.py's own lists (Py.profiles). Blocking, for the session's worker thread
 * (scanner/phone.py reads it from there); GATT callbacks arrive on a binder thread.
 */

fun uuid16(short: String): UUID = UUID.fromString("0000${short.lowercase()}-0000-1000-8000-00805f9b34fb")

private val CCCD = uuid16("2902")

class Channel(val label: String, val service: BluetoothGattService, val notify: BluetoothGattCharacteristic,
              val write: BluetoothGattCharacteristic) {
    val noResponse get() = write.properties and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0
    override fun toString() = "$label: service ${service.uuid}, notify ${notify.uuid}, write ${write.uuid} " +
        "(${if (noResponse) "without response" else "with response"})"
}

private fun notifies(c: BluetoothGattCharacteristic) =
    c.properties and (BluetoothGattCharacteristic.PROPERTY_NOTIFY or BluetoothGattCharacteristic.PROPERTY_INDICATE) != 0

private fun writes(c: BluetoothGattCharacteristic) =
    c.properties and (BluetoothGattCharacteristic.PROPERTY_WRITE or BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE) != 0

/** Candidate channels in the order to try them. */
fun channels(services: List<BluetoothGattService>): List<Channel> {
    val out = mutableListOf<Channel>()
    for (p in Py.profiles) {
        val s = services.firstOrNull { it.uuid == p.service } ?: continue
        val n = s.getCharacteristic(p.notify)
        val w = s.getCharacteristic(p.write)
        if (n != null && w != null) out.add(Channel(p.label, s, n, w))
    }
    val known = out.map { it.service.uuid }.toSet()
    for (s in services) {
        if (s.uuid in known || s.uuid in Py.skipServices) continue
        val ns = s.characteristics.filter(::notifies)
        val ws = s.characteristics.filter(::writes)
        val both = ns.filter { it in ws }
        if (both.isNotEmpty()) out.add(Channel("generic", s, both[0], both[0]))
        else if (ns.isNotEmpty() && ws.isNotEmpty()) out.add(Channel("generic", s, ns[0], ws[0]))
    }
    return out
}

@SuppressLint("MissingPermission")  // the UI asks for BLUETOOTH_CONNECT before any link is opened
class BleLink(private val ctx: Context, val target: String, private val connectTimeoutMs: Long = 20000) : Link {

    private sealed class Ev {
        data class State(val status: Int, val state: Int) : Ev()
        data class Mtu(val mtu: Int, val status: Int) : Ev()
        data class Services(val status: Int) : Ev()
        data class Wrote(val status: Int) : Ev()
        data class Desc(val status: Int) : Ev()
    }

    private val events = LinkedBlockingQueue<Ev>()
    private val buf = ByteArrayOutputStream()
    private val lock = Object()
    @Volatile private var lost = false
    private var gatt: BluetoothGatt? = null
    private var device: BluetoothDevice? = null
    var channel: Channel? = null
        private set
    private var mtu = 23

    private val cb = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                synchronized(lock) { lost = true; lock.notifyAll() }
            }
            events.add(Ev.State(status, newState))
        }
        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) { events.add(Ev.Mtu(mtu, status)) }
        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) { events.add(Ev.Services(status)) }
        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) { events.add(Ev.Wrote(status)) }
        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) { events.add(Ev.Desc(status)) }

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) = data(value)

        @Deprecated("Android 12 and older")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            if (Build.VERSION.SDK_INT < 33) @Suppress("DEPRECATION") data(c.value ?: return)
        }
    }

    private fun data(value: ByteArray) {
        synchronized(lock) { buf.write(value); lock.notifyAll() }
    }

    private inline fun <reified T : Ev> await(timeoutMs: Long, what: String): T {
        val end = System.currentTimeMillis() + timeoutMs
        while (true) {
            val left = end - System.currentTimeMillis()
            if (left <= 0) throw LinkException("BLE: no answer to $what within ${timeoutMs / 1000} s")
            val e = events.poll(left, TimeUnit.MILLISECONDS) ?: continue
            if (e is T) return e
            if (e is Ev.State && e.state == BluetoothProfile.STATE_DISCONNECTED) {
                throw LinkException("BLE link dropped during $what (status ${e.status}). Is a phone app or laptop still connected to the adapter?")
            }
        }
    }

    fun open(): BleLink {
        val bt = (ctx.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
            ?: throw LinkException("This phone has no Bluetooth")
        if (!bt.isEnabled) throw LinkException("Bluetooth is off. Turn it on and retry.")
        val dev = try { bt.getRemoteDevice(target) } catch (e: IllegalArgumentException) { throw LinkException("not a Bluetooth address: $target") }
        device = dev
        var lastErr: Exception? = null
        for (attempt in 0..1) {  // status 133 on the first connect is common on Android; one more go
            events.clear()
            lost = false
            gatt = dev.connectGatt(ctx, false, cb, BluetoothDevice.TRANSPORT_LE)
            try {
                val st = await<Ev.State>(connectTimeoutMs, "connect")
                if (st.state != BluetoothProfile.STATE_CONNECTED) throw LinkException("BLE connect failed (status ${st.status})")
                lastErr = null
                break
            } catch (e: LinkException) {
                lastErr = e
                gatt?.close()
                gatt = null
                Thread.sleep(600)
            }
        }
        lastErr?.let {
            throw LinkException("${it.message}. Adapter plugged in and lit? No other phone app connected to it? BLE adapters take one connection at a time.", it)
        }
        val g = gatt!!
        try {
            if (g.requestMtu(247)) {
                try { mtu = await<Ev.Mtu>(3000, "MTU request").mtu } catch (_: LinkException) {}
            }
            if (!g.discoverServices()) throw LinkException("BLE: service discovery didn't start")
            val s = await<Ev.Services>(10000, "service discovery")
            if (s.status != BluetoothGatt.GATT_SUCCESS) throw LinkException("BLE: service discovery failed (status ${s.status})")
            val cands = channels(g.services)
            if (cands.isEmpty()) {
                throw LinkException("no notify + write characteristic pair on this device; is it an OBD adapter?\n" + describe().joinToString("\n"))
            }
            for (ch in cands) {
                enableNotify(ch, true)
                if (cands.size == 1 || probe(ch)) { channel = ch; break }
                enableNotify(ch, false)
            }
            if (channel == null) throw LinkException("no characteristic pair answered ATI:\n" + cands.joinToString("\n"))
            resetInput()
        } catch (e: Exception) {
            close()
            throw if (e is LinkException) e else LinkException("BLE: ${e.message}", e)
        }
        return this
    }

    private fun enableNotify(ch: Channel, on: Boolean) {
        val g = gatt ?: throw LinkException("BLE link closed")
        g.setCharacteristicNotification(ch.notify, on)
        val d = ch.notify.getDescriptor(CCCD) ?: return
        val value = when {
            !on -> BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE
            ch.notify.properties and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0 -> BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            else -> BluetoothGattDescriptor.ENABLE_INDICATION_VALUE
        }
        val started = if (Build.VERSION.SDK_INT >= 33) {
            g.writeDescriptor(d, value) == BluetoothGatt.GATT_SUCCESS
        } else {
            @Suppress("DEPRECATION")
            run { d.value = value; g.writeDescriptor(d) }
        }
        if (started) try { await<Ev.Desc>(3000, "enabling notifications") } catch (_: LinkException) { if (lost) throw LinkException("BLE link dropped") }
    }

    /** Some clones expose two pipes and wire only one to the chip. ATI is read-only. */
    private fun probe(ch: Channel): Boolean {
        resetInput()
        writeTo(ch, "ATI\r".toByteArray())
        val end = System.currentTimeMillis() + 2000
        while (System.currentTimeMillis() < end) {
            synchronized(lock) { if (buf.toByteArray().contains('>'.code.toByte())) return true }
            Thread.sleep(50)
        }
        synchronized(lock) { return buf.size() > 0 }
    }

    private fun writeTo(ch: Channel, data: ByteArray) {
        val g = gatt ?: throw LinkException("BLE link closed")
        val type = if (ch.noResponse) BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE else BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        val size = maxOf(20, mtu - 3)
        var i = 0
        while (i < data.size) {
            val chunk = data.copyOfRange(i, minOf(data.size, i + size))
            events.removeIf { it is Ev.Wrote }
            val ok = if (Build.VERSION.SDK_INT >= 33) {
                g.writeCharacteristic(ch.write, chunk, type) == BluetoothGatt.GATT_SUCCESS
            } else {
                @Suppress("DEPRECATION")
                run { ch.write.writeType = type; ch.write.value = chunk; g.writeCharacteristic(ch.write) }
            }
            if (!ok) {
                if (lost) throw LinkException("BLE link dropped")
                throw LinkException("BLE write refused")
            }
            await<Ev.Wrote>(5000, "a write")
            i += size
        }
    }

    override fun write(data: ByteArray) {
        if (lost) throw LinkException("BLE link dropped")
        writeTo(channel ?: throw LinkException("BLE link not open"), data)
    }

    override fun read(timeoutMs: Long): ByteArray {
        synchronized(lock) {
            if (buf.size() == 0) {
                if (lost) throw LinkException("BLE link dropped")
                lock.wait(maxOf(1, timeoutMs))
            }
            if (buf.size() == 0 && lost) throw LinkException("BLE link dropped")
            val b = buf.toByteArray()
            buf.reset()
            return b
        }
    }

    override fun resetInput() = synchronized(lock) { buf.reset() }

    override fun close() {
        val g = gatt ?: return
        gatt = null
        try { g.disconnect() } catch (_: Exception) {}
        try { g.close() } catch (_: Exception) {}
        synchronized(lock) { lost = true; lock.notifyAll() }
    }

    private fun describe(): List<String> {
        val out = mutableListOf<String>()
        for (s in gatt?.services ?: emptyList()) {
            out.add("  service ${s.uuid}")
            for (c in s.characteristics) out.add("    ${c.uuid}  props 0x%02X".format(c.properties))
        }
        return out
    }

    override fun info(): List<String> =
        listOf("BLE device ${device?.address} (${device?.name ?: "no name"})", "channel $channel", "MTU $mtu") + describe()
}
