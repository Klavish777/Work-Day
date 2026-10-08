package com.workday.trader

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.KeyEvent
import android.webkit.JavascriptInterface
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.Toast

/**
 * Work-Day AI Trader — Android client.
 *
 * The trading backend (Python: MT5 gateway + LLM multi-agent engine) runs on
 * the PC next to the MetaTrader 5 terminal. This app is the remote
 * dashboard/control panel: it loads the backend's web UI inside a WebView.
 *
 * UX guarantees:
 *  - first run always asks for the server address;
 *  - if the server is unreachable a readable error screen with
 *    "change address" / "retry" buttons is shown (never a blank page);
 *  - the floating "⚙ Адрес" button reopens the address dialog at any time.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private var serverUrl: String = ""
    private var dialogShown = false

    companion object {
        private const val PREF_KEY = "server_url"
    }

    // ------------------------------------------------------------------ //
    @SuppressLint("SetJavaScriptEnabled", "AddJavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }
        webView.webViewClient = Client()
        webView.addJavascriptInterface(Bridge(), "AndroidBridge")

        val d = resources.displayMetrics.density
        val root = FrameLayout(this)
        root.addView(
            webView,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
        )
        // Floating button: change the backend address at any moment.
        val gear = Button(this)
        gear.text = "⚙ Адрес"
        gear.alpha = 0.85f
        val lp = FrameLayout.LayoutParams((118 * d).toInt(), (52 * d).toInt())
        lp.gravity = Gravity.BOTTOM or Gravity.END
        lp.setMargins(0, 0, (14 * d).toInt(), (70 * d).toInt())
        gear.layoutParams = lp
        gear.setOnClickListener { showServerDialog(false) }
        root.addView(gear)
        setContentView(root)

        serverUrl = getPreferences(MODE_PRIVATE).getString(PREF_KEY, "") ?: ""
        if (serverUrl.isBlank()) {
            showServerDialog(true)
        } else {
            webView.loadUrl(serverUrl)
        }
    }

    // ------------------------------------------------------------------ //
    private inner class Client : WebViewClient() {
        @Deprecated("Deprecated in Java")
        override fun onReceivedError(
            view: WebView, errorCode: Int, description: String?, failingUrl: String?
        ) {
            // react only to the main page failing, not subresources
            if (failingUrl != null &&
                (failingUrl == serverUrl || failingUrl == "$serverUrl/")
            ) {
                showErrorPage()
            }
        }
    }

    /** JS hooks for the built-in error page. */
    private inner class Bridge {
        @JavascriptInterface
        fun changeServer() {
            runOnUiThread { showServerDialog(false) }
        }

        @JavascriptInterface
        fun reload() {
            runOnUiThread {
                if (serverUrl.isNotBlank()) webView.loadUrl(serverUrl)
            }
        }
    }

    // ------------------------------------------------------------------ //
    private fun showErrorPage() {
        val html = """
        <!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          body{background:#0b0f17;color:#e8eefc;font-family:sans-serif;margin:0;
               display:flex;flex-direction:column;align-items:center;justify-content:center;
               min-height:100vh;text-align:center;padding:24px;box-sizing:border-box}
          h2{margin-bottom:12px}
          p{color:#8ba0c4;font-size:14px;line-height:1.7;max-width:430px}
          b{color:#e8eefc}
          button{background:#38bdf8;border:none;border-radius:10px;padding:14px 26px;
                 font-size:15px;font-weight:700;margin:6px;color:#08101e}
        </style></head><body>
        <h2>Нет связи с сервером</h2>
        <p>Приложение не смогло подключиться к торговому серверу.<br><br>
        Проверьте:<br>
        1. На ПК с MetaTrader 5 запущен <b>python main.py</b><br>
        2. Телефон и ПК находятся <b>в одной сети</b> (один Wi-Fi)<br>
        3. Адрес введён как <b>http://IP-ПК:8080</b><br>
        (IP компьютера виден в результате команды <b>ipconfig</b>)</p>
        <div>
          <button onclick="AndroidBridge.changeServer()">Изменить адрес</button>
          <button onclick="AndroidBridge.reload()">Повторить</button>
        </div>
        </body></html>
        """.trimIndent()
        webView.loadDataWithBaseURL("http://workday.local/", html, "text/html", "UTF-8", null)
    }

    // ------------------------------------------------------------------ //
    private fun showServerDialog(firstRun: Boolean) {
        if (dialogShown) return
        dialogShown = true
        val input = EditText(this)
        input.hint = "http://192.168.1.10:8080"
        input.setText(if (serverUrl.isNotBlank()) serverUrl else defaultUrl())
        AlertDialog.Builder(this)
            .setTitle("Адрес сервера Work-Day")
            .setMessage(
                "Укажите адрес ПК, где запущен торговый сервер (компьютер с " +
                "MetaTrader 5). Телефон и ПК должны быть в одной сети."
            )
            .setView(input)
            .setPositiveButton("Подключить") { _, _ ->
                dialogShown = false
                val v = input.text.toString().trim()
                if (v.isNotBlank()) load(v) else if (firstRun) showErrorPage()
            }
            .setNegativeButton("Отмена") { _, _ ->
                dialogShown = false
                if (firstRun) showErrorPage()
            }
            .setCancelable(false)
            .show()
    }

    private fun load(raw: String) {
        var v = raw.trim()
        if (!v.startsWith("http://") && !v.startsWith("https://")) v = "http://$v"
        val hostPart = v.substringAfter("://")
        if (!hostPart.contains(":")) v = "$v:8080"   // default port
        serverUrl = v
        getPreferences(MODE_PRIVATE).edit().putString(PREF_KEY, serverUrl).apply()
        Toast.makeText(this, "Подключение к $serverUrl …", Toast.LENGTH_SHORT).show()
        webView.loadUrl(serverUrl)
    }

    private fun defaultUrl(): String {
        val isEmulator = Build.FINGERPRINT.contains("generic") ||
                Build.MODEL.contains("Emulator") ||
                Build.PRODUCT.contains("sdk")
        return if (isEmulator) "http://10.0.2.2:8080" else ""
    }

    // ------------------------------------------------------------------ //
    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
    }

    override fun onPause() {
        webView.onPause()
        super.onPause()
    }
}
