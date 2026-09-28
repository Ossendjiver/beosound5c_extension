package au.com.homemedia.storage

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import au.com.homemedia.model.AppSettings
import au.com.homemedia.model.appSettingsFromJson
import au.com.homemedia.model.toJson
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("home_media_secure", Context.MODE_PRIVATE)
    private val alias = "home_media_settings_key"

    fun load(): AppSettings {
        val encrypted = prefs.getString("settings", null) ?: return AppSettings()
        return runCatching { appSettingsFromJson(JSONObject(decrypt(encrypted))) }.getOrDefault(AppSettings())
    }

    fun save(settings: AppSettings) {
        prefs.edit().putString("settings", encrypt(settings.toJson().toString())).apply()
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(alias, null) as? SecretKey)?.let { return it }
        val keyGenerator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        keyGenerator.init(
            KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return keyGenerator.generateKey()
    }

    private fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        val iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
        val data = Base64.encodeToString(cipher.doFinal(plain.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
        return "$iv:$data"
    }

    private fun decrypt(value: String): String {
        val (ivEncoded, dataEncoded) = value.split(":", limit = 2)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        val iv = Base64.decode(ivEncoded, Base64.NO_WRAP)
        cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(128, iv))
        return cipher.doFinal(Base64.decode(dataEncoded, Base64.NO_WRAP)).toString(Charsets.UTF_8)
    }
}
