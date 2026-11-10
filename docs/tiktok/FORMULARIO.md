# Formulario de la app en developers.tiktok.com — qué poner en cada campo

Copia y pega. Los textos van en inglés porque la revisión la hace TikTok en inglés.

## Basic information

| Campo | Valor |
|---|---|
| **App icon** | `docs/tiktok/clipstream_icon_1024.png` (1024×1024 PNG) |
| **App name** | `ClipStream` |
| **Category** | La más parecida a *Video* / *Entertainment* / *Tools* que ofrezca la lista |
| **Description** (máx. 120) | ver abajo (120 caracteres justos) |
| **Terms of Service URL** | la de `terms.html` una vez publicada (ver «Páginas legales») |
| **Privacy Policy URL** | la de `privacy.html` una vez publicada |
| **Platforms** | solo **Desktop** |

**Description**

```
Desktop app for creators: turns your own streams and videos into vertical clips with subtitles and posts them to TikTok.
```

## App review — explicación (máx. 1000; este texto tiene 932)

```
ClipStream is a Windows desktop app for creators. It analyses the creator's own streams and videos locally and cuts them into vertical clips with subtitles.

Login Kit (Desktop, PKCE, redirect to http://127.0.0.1): in the Export screen the creator presses "Connect with TikTok", authorizes in the browser and returns to the app.

user.info.basic: we show the connected account (avatar and display name) so the creator always knows where a clip will be posted.

Content Posting API / video.publish (Direct Post): the creator selects a clip and opens the publish dialog. It queries creator_info, shows the account and a preview, lets them edit the caption, choose the privacy level manually (no default), allow comments/duet/stitch (off by default), disclose commercial content and accept the Music Usage Confirmation. Only after pressing Publish is the file uploaded with FILE_UPLOAD; the app then polls the post status and shows it.
```

## Products (Add products)

1. **Login Kit**
   - Plataforma Desktop → **Redirect URI**: `http://127.0.0.1:8765/api/publish/tiktok/callback/`
     (exactamente así, con la barra final; es la que muestra ClipStream en Exportar → TikTok).
2. **Content Posting API**
   - Activa **Direct Post**.
   - No hace falta verificar dominios (eso solo es para PULL_FROM_URL; ClipStream sube el archivo).

## Scopes (Add scopes)

- `user.info.basic`
- `video.publish`

No añadas más: TikTok retrasa la revisión si hay permisos que no salen en el vídeo.

## Demo video

Se graba **después** de probar en **Sandbox** (pestaña Sandbox arriba): añade tu cuenta como usuario de
prueba, pon tu cuenta de TikTok en privado, pega en ClipStream el Client Key/Secret **del sandbox** y
conecta. Guion del vídeo en `GUIA_REVISION.md`, punto 4.

## Orden recomendado

1. Rellenar Basic information, Products y Scopes → **Save** (sin enviar).
2. Publicar las páginas legales y pegar sus URLs.
3. Sandbox: probar la conexión y una publicación real (privada) desde ClipStream.
4. Grabar el vídeo de demostración, subirlo → **Submit for review**.
