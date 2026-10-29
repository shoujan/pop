package com.example.androidlab

import android.app.Activity
import android.os.BatteryManager
import android.os.Bundle
import android.content.Context
import android.graphics.Typeface
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("android_lab", Context.MODE_PRIVATE) }
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var serverInput: EditText
    private lateinit var codeInput: EditText
    private lateinit var status: TextView
    private lateinit var pairButton: Button
    private lateinit var checkinButton: Button
    private lateinit var disconnectButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 32, 28, 28)
            setBackgroundColor(0xFFF4F6FA.toInt())
        }
        val scroll = ScrollView(this)
        val content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        content.addView(TextView(this).apply {
            text = "Android Lab Client"
            textSize = 26f
            setTextColor(0xFF14213D.toInt())
            typeface = Typeface.DEFAULT_BOLD
        }, match())
        content.addView(TextView(this).apply {
            text = "Visible, consent-based connection to your Windows security lab. Pair only on a trusted local Wi-Fi network."
            textSize = 15f
            setPadding(0, 12, 0, 18)
        }, match())
        serverInput = EditText(this).apply {
            hint = "Windows IP address, e.g. 192.168.1.20"
            singleLine = true
            setText(prefs.getString("server", ""))
        }
        content.addView(serverInput, match())
        codeInput = EditText(this).apply {
            hint = "6-digit pairing code"
            inputType = 2
            singleLine = true
        }
        content.addView(codeInput, match())
        pairButton = Button(this).apply {
            text = "Pair this device"
            setOnClickListener { pair() }
        }
        content.addView(pairButton, match())
        status = TextView(this).apply {
            text = if (prefs.getString("token", null) == null) "Status: not paired" else "Status: paired (tap Check in to verify)"
            textSize = 15f
            setPadding(0, 18, 0, 12)
        }
        content.addView(status, match())
        checkinButton = Button(this).apply {
            text = "Send approved diagnostics"
            setOnClickListener { sendDiagnostics() }
        }
        content.addView(checkinButton, match())
        disconnectButton = Button(this).apply {
            text = "Disconnect and forget token"
            setOnClickListener { disconnect() }
        }
        content.addView(disconnectButton, match())
        content.addView(TextView(this).apply {
            text = "Collected data: device model, Android version, app version, and battery percentage/charging state only. No contacts, messages, files, microphone, camera, location, or remote commands."
            textSize = 13f
            setPadding(0, 18, 0, 0)
        }, match())
        scroll.addView(content)
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        updateButtons()
    }

    private fun match() = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)

    private fun baseUrl(): String {
        val host = serverInput.text.toString().trim().removePrefix("http://").removeSuffix("/")
        require(host.isNotBlank()) { "Enter the Windows IP address." }
        require(!host.contains("/")) { "Enter only the IP address, without a path." }
        return "http://$host:8765"
    }

    private fun request(path: String, method: String, body: JSONObject? = null, token: String? = null): JSONObject {
        val connection = (URL(baseUrl() + path).openConnection() as HttpURLConnection)
        connection.requestMethod = method
        connection.connectTimeout = 5000
        connection.readTimeout = 5000
        connection.setRequestProperty("Accept", "application/json")
        if (token != null) connection.setRequestProperty("Authorization", "Bearer $token")
        if (body != null) {
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val response = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
        connection.disconnect()
        if (code !in 200..299) {
            val detail = try { JSONObject(response).optString("detail", response) } catch (_: Exception) { response }
            throw IllegalStateException("HTTP $code: $detail")
        }
        return JSONObject(response)
    }

    private fun runAsync(action: () -> String) {
        setBusy(true)
        executor.execute {
            val result = try { action() } catch (e: Exception) { "Error: ${e.message ?: e.javaClass.simpleName}" }
            runOnUiThread {
                status.text = result
                setBusy(false)
                updateButtons()
            }
        }
    }

    private fun pair() {
        val code = codeInput.text.toString().trim()
        if (!code.matches(Regex("[0-9]{6}"))) {
            status.text = "Enter the 6-digit code displayed on Windows."
            return
        }
        runAsync {
            val server = baseUrl()
            val response = request("/pair", "POST", JSONObject().apply {
                put("code", code)
                put("device_name", android.os.Build.DEVICE.take(80))
                put("model", android.os.Build.MODEL.take(100))
                put("android_version", android.os.Build.VERSION.RELEASE.take(40))
                put("app_version", "1.0.0")
            })
            prefs.edit().putString("server", server.removePrefix("http://").removeSuffix(":8765"))
                .putString("token", response.getString("token")).apply()
            "Paired successfully. ${response.optString("message")}" 
        }
    }

    private fun sendDiagnostics() {
        val token = prefs.getString("token", null)
        if (token == null) { status.text = "Pair this device first."; return }
        runAsync {
            val manager = getSystemService(BATTERY_SERVICE) as BatteryManager
            val level = manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY).coerceIn(0, 100)
            val battery = registerReceiver(null, android.content.IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED))
            val plugged = battery?.getIntExtra(android.os.BatteryManager.EXTRA_PLUGGED, 0) ?: 0
            val isCharging = plugged != 0
            val body = JSONObject().apply {
                put("battery_percent", level)
                put("battery_charging", isCharging)
                put("android_version", android.os.Build.VERSION.RELEASE.take(40))
                put("model", android.os.Build.MODEL.take(100))
                put("app_version", "1.0.0")
            }
            val response = request("/diagnostics", "POST", body, token)
            "Diagnostics sent with your action. Server status: ${response.optString("status")}."
        }
    }

    private fun disconnect() {
        val token = prefs.getString("token", null)
        if (token == null) {
            prefs.edit().remove("token").apply()
            status.text = "Local token cleared."
            updateButtons()
            return
        }
        runAsync {
            try { request("/disconnect", "POST", JSONObject(), token) } finally { prefs.edit().remove("token").apply() }
            "Disconnected. Pairing token cleared from this phone."
        }
    }

    private fun setBusy(busy: Boolean) {
        pairButton.isEnabled = !busy
        checkinButton.isEnabled = !busy
        disconnectButton.isEnabled = !busy
    }

    private fun updateButtons() {
        val paired = prefs.getString("token", null) != null
        pairButton.isEnabled = !paired
        checkinButton.isEnabled = paired
        disconnectButton.isEnabled = true
    }

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }
}
