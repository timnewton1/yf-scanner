import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

// The scanner itself is the repo's Python: scanner/ and summarize.py, copied in as they are.
val repo = rootDir.parentFile
val pythonSrc = layout.buildDirectory.dir("yf-python")
val syncPython = tasks.register<Sync>("syncScannerPython") {
    from(repo) {
        include("scanner/**/*.py", "summarize.py")
        exclude("**/__pycache__/**")
    }
    into(pythonSrc)
}
val local = Properties().apply { rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) } }

android {
    namespace = "yf.scanner.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "yf.scanner.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "0.2"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }  // phones, and the emulator
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
}

chaquopy {
    defaultConfig {
        version = "3.14"
        // A Python 3.14 on this machine compiles the .pyc files and runs pip. setup/android-sdk.sh writes it here.
        local.getProperty("yf.buildPython")?.let { buildPython(it) }
        pip { install("pyserial") }  // bleak and textual stay on the laptop: BLE is BleLink.kt here
    }
    sourceSets { getByName("main") { srcDir(pythonSrc) } }
}
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }.configureEach { dependsOn(syncPython) }

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
}
