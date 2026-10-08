plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.workday.trader"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.workday.trader"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
        // Default backend address. Change it in the app UI at runtime
        // (Settings -> server URL) — stored in SharedPreferences.
        buildConfigField("String", "DEFAULT_SERVER_URL", "\"http://10.0.2.2:8080\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Debug signing for a quick installable APK; configure your own
            // keystore for production builds.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation(kotlin("stdlib"))
}
