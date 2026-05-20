package com.rowenta.mapeditor

import android.content.Context
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Local HTTP server that:
 *  1. Serves the map editor HTML/CSS/JS from Android assets.
 *  2. Proxies /get/... and /set/... requests to the Rowenta robot (port 8080).
 *  3. Exposes /config GET/POST so the UI can read and update the robot IP at runtime.
 *
 * Mirrors the logic of rowenta-editor-server.py so the existing web editor JS works
 * unchanged inside the WebView.
 */
class MapEditorServer(
    private val context: Context,
    port: Int = SERVER_PORT,
) : NanoHTTPD(port) {

    @Volatile
    var robotIp: String = ""

    companion object {
        const val SERVER_PORT = 8765
        const val ROBOT_PORT = 8080
        private val PROXY_MARKERS = listOf("/get/", "/set/", "/js/", "/rowenta-map-editor.css", "/config")
    }

    override fun serve(session: IHTTPSession): Response {
        val path = cleanPath(session.uri)
        val query = session.queryParameterString ?: ""

        return when {
            path == "/" || path.endsWith(".html") ->
                serveAsset("rowenta-map-editor.html", MIME_HTML)

            path == "/rowenta-map-editor.css" ->
                serveAsset("rowenta-map-editor.css", "text/css")

            path.startsWith("/js/") ->
                serveAsset(path.removePrefix("/"), "application/javascript")

            path == "/config" && session.method == Method.GET ->
                serveConfig()

            path == "/config" && session.method == Method.POST ->
                handleConfigPost(session)

            path.startsWith("/get/") || path.startsWith("/set/") ->
                proxyToRobot(path, query)

            else ->
                newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "Not found: $path")
        }
    }

    /** Strip HA ingress path prefix — keeps this server compatible with add-on mode too. */
    private fun cleanPath(uri: String): String {
        for (marker in PROXY_MARKERS) {
            val idx = uri.indexOf(marker)
            if (idx >= 0) return uri.substring(idx)
        }
        return uri.ifEmpty { "/" }
    }

    private fun serveAsset(assetPath: String, mimeType: String): Response {
        return try {
            val stream = context.assets.open(assetPath)
            newChunkedResponse(Response.Status.OK, mimeType, stream)
        } catch (e: IOException) {
            newFixedLengthResponse(
                Response.Status.NOT_FOUND,
                MIME_PLAINTEXT,
                "Asset not found: $assetPath",
            )
        }
    }

    private fun serveConfig(): Response {
        val json = JSONObject().apply {
            put("robot_ip", robotIp)
            put("proxy_mode", true)
        }.toString()
        return newFixedLengthResponse(Response.Status.OK, "application/json", json)
    }

    private fun handleConfigPost(session: IHTTPSession): Response {
        return try {
            val bodyMap = mutableMapOf<String, String>()
            session.parseBody(bodyMap)
            val raw = bodyMap["postData"] ?: ""
            val obj = JSONObject(raw)
            if (obj.has("robot_ip")) {
                robotIp = obj.getString("robot_ip").trim()
            }
            newFixedLengthResponse(
                Response.Status.OK,
                "application/json",
                """{"ok":true}""",
            )
        } catch (e: Exception) {
            newFixedLengthResponse(
                Response.Status.BAD_REQUEST,
                "application/json",
                """{"error":"${e.message}"}""",
            )
        }
    }

    private fun proxyToRobot(path: String, query: String): Response {
        val ip = robotIp.trim()
        if (ip.isEmpty()) {
            return newFixedLengthResponse(
                Response.Status.BAD_GATEWAY,
                "application/json",
                """{"error":"Robot IP not set. Enter it in the editor and tap Connect."}""",
            )
        }

        val url = buildString {
            append("http://")
            append(ip)
            append(":")
            append(ROBOT_PORT)
            append(path)
            if (query.isNotEmpty()) {
                append("?")
                append(query)
            }
        }

        return try {
            val conn = URL(url).openConnection() as HttpURLConnection
            conn.connectTimeout = 15_000
            conn.readTimeout = 15_000
            conn.requestMethod = "GET"
            conn.connect()

            val code = conn.responseCode
            val contentType = conn.contentType ?: "application/json"
            val body = try {
                conn.inputStream.readBytes()
            } catch (e: IOException) {
                conn.errorStream?.readBytes() ?: ByteArray(0)
            }
            conn.disconnect()

            val status = Response.Status.lookup(code) ?: Response.Status.INTERNAL_ERROR
            newFixedLengthResponse(status, contentType, body.toString(Charsets.UTF_8))
        } catch (e: Exception) {
            newFixedLengthResponse(
                Response.Status.BAD_GATEWAY,
                "application/json",
                JSONObject().put("error", e.message ?: "Connection failed").toString(),
            )
        }
    }
}
