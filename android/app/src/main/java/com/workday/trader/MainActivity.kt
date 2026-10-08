package com.workday.trader

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.view.KeyEvent
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast

/**
 * Work-Day AI Trader — Android client.
 *
 * The trading backend (Python: MT5 gateway + LLM multi-agent engine) runs on
 * your PC next to the MetaTrader 5 terminal. This app is the remote
 * dashboard/control panel: it loads the backend's web UI inside a WebView.
 *
 * First run: you are asked for the server address, e.g.
 *   http://192.168.1.10:8080   (your PC's LAN IP)
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private var serverUrl: String = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)
        configureWebView()

        serverUrl = getPreferences(MODE_PRIVATE)
            .getString("server_url", BuildConfig.DEFAULT_SERVER_URL) ?: ""

        if (serverUrl.isBlank()) {
            showServerDialog()
        } else {
            load(serverUrl)
        }
    }

    private fun configureWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }
        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedError(
                view: WebView, errorCode: Int, description: String, failingUrl: String
            ) {
                Toast.makeText(
                    this@MainActivity,
                    "Cannot reach $failingUrl — check the server address (menu → Server).",
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }

    private fun load(url: String) {
        serverUrl = if (url.startsWith("http")) url else "http://$url"
        getPreferences(MODE_PRIVATE).edit().putString("server_url", serverUrl).apply()
        webView.loadUrl(serverUrl)
    }

    private fun showServerDialog() {
        val input = EditText(this).apply {
            hint = "http://192.168.1.10:8080"
            setText(serverUrl)
        }
        AlertDialog.Builder(this)
            .setTitle("Work-Day server address")
            .setMessage("Enter the address of the PC running the trading backend " +
                    "(the machine with MetaTrader 5).")
            .setView(input)
            .setPositiveButton("Connect") { _, _ -> load(input.text.toString().trim()) }
            .setNegativeButton("Cancel") { _, _ -> }
            .setCancelable(false)
            .show()
    }

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

    /** Call from menu / long-press to change the backend address. */
    fun changeServer() = showServerDialog()
}
