pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
    repositories { google(); mavenCentral() }
}
rootProject.name = "yf-scanner"
include(":app")  // needs the Android SDK and a build Python: setup/android-sdk.sh
