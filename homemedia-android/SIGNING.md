# Home Media Android signing

Home Media uses the stable Android application ID `au.com.homemedia`.

Android will only install a newer APK over an existing installation when both APKs are signed by the same signing certificate. Keeping the same application ID is necessary but not sufficient.

## Persistent release signing

Generate one private keystore once and keep it backed up securely. Do not commit it to this repository.

Example:

```bash
keytool -genkeypair -v \
  -keystore home-media-release.jks \
  -alias homemedia \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000
```

Base64-encode the keystore and add these GitHub Actions repository secrets:

- `HOME_MEDIA_KEYSTORE_B64` — base64 contents of the JKS/keystore
- `HOME_MEDIA_KEYSTORE_PASSWORD` — keystore password
- `HOME_MEDIA_KEY_ALIAS` — key alias, for example `homemedia`
- `HOME_MEDIA_KEY_PASSWORD` — key password

On Linux/macOS:

```bash
base64 < home-media-release.jks | tr -d '\n'
```

Once these secrets are configured, the Android workflow emits `app-release-signed.apk`. Install that APK and use only APKs signed with this same keystore for all future updates.

Keep an offline copy of the keystore and passwords. Losing the signing key means future builds cannot update an installation signed with it.

## Existing debug installations

GitHub-hosted runners create temporary debug signing keys. A debug APK produced by one workflow run is therefore not guaranteed to update a debug APK produced by another run.

If an existing installation was signed with a different key, Android requires uninstalling it before installing the new signing lineage. Export the full Home Media settings backup first whenever possible.
