package yf.scanner.app

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { YfTheme { AppUi(this) } }
    }

    fun keepScreenOn(on: Boolean) {
        if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }
}

@Composable
fun YfTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val ctx = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

private val TABS = listOf("Connect", "Health", "Live", "Mode 06", "Info", "Console", "Logs")

/** The Bluetooth permissions this Android version needs for scanning and connecting. */
fun btPermissions(): Array<String> = when {
    Build.VERSION.SDK_INT >= 33 -> arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.POST_NOTIFICATIONS)
    Build.VERSION.SDK_INT >= 31 -> arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    else -> arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
}

private fun required(p: String) = p != Manifest.permission.POST_NOTIFICATIONS

fun hasBt(ctx: Context) = btPermissions().filter(::required).all { ctx.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

fun btOn(ctx: Context) = (ctx.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter?.isEnabled == true

/** withBluetooth { ... }: asks for the permissions and to turn Bluetooth on if needed, then runs it. */
@Composable
fun rememberWithBluetooth(): ((() -> Unit) -> Unit) {
    val ctx = LocalContext.current
    var pending by remember { mutableStateOf<(() -> Unit)?>(null) }
    val enable = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (btOn(ctx)) pending?.invoke() else Scanner.toast.value = "Bluetooth is off"
        pending = null
    }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { res ->
        if (res.filterKeys(::required).values.all { it }) {
            if (btOn(ctx)) { pending?.invoke(); pending = null }
            else enable.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE))
        } else {
            Scanner.toast.value = "The app needs the Bluetooth permission to reach the adapter"
            pending = null
        }
    }
    return { action ->
        pending = action
        when {
            !hasBt(ctx) -> perms.launch(btPermissions())
            !btOn(ctx) -> try { enable.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)) } catch (_: SecurityException) { perms.launch(btPermissions()) }
            else -> { action(); pending = null }
        }
    }
}

@Composable
fun AppUi(activity: MainActivity) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val busy by Scanner.busy.collectAsState()
    val log by Scanner.log.collectAsState()
    val toast by Scanner.toast.collectAsState()
    val ctx = LocalContext.current

    LaunchedEffect(log.running) { activity.keepScreenOn(log.running) }
    LaunchedEffect(toast) {
        toast?.let { Toast.makeText(ctx, it, Toast.LENGTH_LONG).show(); Scanner.toast.value = null }
    }

    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(Modifier.safeDrawingPadding()) {
            ScrollableTabRow(selectedTabIndex = tab, edgePadding = 0.dp) {
                TABS.forEachIndexed { i, t ->
                    Tab(selected = tab == i, onClick = { tab = i }, text = { Text(if (i == 2 && log.running) "● Live" else t) })
                }
            }
            if (busy != null) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
                Text(busy!!, style = MaterialTheme.typography.labelMedium, modifier = Modifier.pad())
            }
            when (tab) {
                0 -> ConnectTab()
                1 -> HealthTab()
                2 -> LiveTab()
                3 -> Mode06Tab()
                4 -> InfoTab()
                5 -> ConsoleTab()
                6 -> LogsTab()
            }
        }
    }
}
