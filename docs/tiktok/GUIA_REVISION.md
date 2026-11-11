# Conectar ClipStream con TikTok y pasar la revisión

Guía práctica basada en la documentación oficial de TikTok for Developers (consultada en noviembre de 2026):
[crear una app](https://developers.tiktok.com/doc/getting-started-create-an-app),
[Login Kit para escritorio](https://developers.tiktok.com/doc/login-kit-desktop),
[Content Posting API](https://developers.tiktok.com/doc/content-posting-api-get-started),
[normas de integración (Content Sharing Guidelines)](https://developers.tiktok.com/doc/content-sharing-guidelines),
[guía de revisión de apps](https://developers.tiktok.com/doc/app-review-guidelines).

---

## Antes de empezar: dos normas de TikTok que afectan a ClipStream

1. **Uso personal.** TikTok no aprueba apps «para uso privado o personal» y pone como ejemplo de lo
   que NO acepta: «una herramienta para subir contenido a las cuentas que gestionas tú o tu equipo».
   Si ClipStream se presenta como herramienta solo para tus cuentas, la auditoría (publicar en público)
   probablemente se rechace. Para aprobarla hay que presentarla como **producto para otros creadores**
   (por ejemplo: «herramienta de escritorio para que streamers conviertan SUS directos en clips
   verticales y los publiquen en su cuenta»), con web pública, política de privacidad y condiciones.
2. **Contenido de otras plataformas.** TikTok tampoco acepta «una app que copia contenido arbitrario
   de otras plataformas a TikTok». Publicar clips de directos de **otros** creadores va contra esto y
   contra sus derechos. La presentación (y el uso) debe ser: cada creador publica clips de **su propio
   contenido** o de contenido para el que tiene permiso.

**Sin auditoría** la integración funciona igualmente, con límites: todo lo publicado queda **privado
(«Solo yo»)**, la **cuenta debe ser privada** al publicar y como máximo 5 usuarios en 24 h. Sirve para
probar y para el vídeo de demostración.

---

## 1. Crear la app (developers.tiktok.com)

1. Entra en <https://developers.tiktok.com> con tu cuenta de TikTok → **Manage apps → Connect an app**.
   Algunas guías indican que se necesita una cuenta de **organización** para la revisión: si la pide, créala.
2. **App details**
   - **App name:** `ClipStream` (no puede mencionar a TikTok ni a otras redes).
   - **Icon:** `installer/clipstream.ico` exportado a PNG 1024×1024.
   - **Category:** Video / Entertainment tools.
   - **Description** (en inglés, la revisión es en inglés):
     > ClipStream is a Windows desktop app for live streamers and video creators. It analyses the
     > creator's own streams and videos locally on their PC, cuts the best moments into vertical
     > 9:16 clips with subtitles, and lets the creator review, edit and publish each clip to their
     > TikTok account. Every post is reviewed and confirmed by the creator before it is uploaded.
   - **Terms of Service URL** y **Privacy Policy URL:** las páginas de `docs/site/` publicadas
     (ver punto 6). Deben estar activas y visibles sin abrir menús.
   - **Website URL:** la portada `docs/site/index.html` publicada (no puede ser solo una página de login).
   - **Platforms:** **Desktop**.
3. **Products → Add products:**
   - **Login Kit** → Redirect URI (Desktop):
     `http://127.0.0.1:8765/api/publish/tiktok/callback/`
     (cópiala desde ClipStream → Exportar → panel TikTok; debe coincidir exactamente, con la barra final).
   - **Content Posting API** → activa **Direct Post**.
4. **Scopes:** `user.info.basic` y `video.publish`. No pidas más de los necesarios.
5. Copia el **Client Key** y el **Client Secret** y pégalos en ClipStream → Exportar → panel TikTok.
   Se guardan cifrados en tu PC. **No los compartas ni los subas a GitHub.**

## 2. Probar en sandbox

1. En la app, pestaña **Sandbox** → crea un sandbox y añade tu cuenta de TikTok como **target user**.
2. Pon tu cuenta de TikTok en **privada** (requisito mientras la app no esté auditada).
3. En ClipStream: Exportar → **Conectar con TikTok** → autoriza en el navegador → publica un clip.
   Saldrá como «Solo yo».

## 3. Explicación de cada permiso (campo «App review»)

- **Login Kit / user.info.basic:** «Used to show the creator which TikTok account is connected
  (avatar and nickname) in ClipStream's export screen, so they always know where a clip will be posted.»
- **Content Posting API / video.publish:** «Used to upload a vertical clip that the creator has
  selected, reviewed and confirmed in ClipStream's publish dialog (title, privacy level chosen
  manually, interaction settings, commercial content disclosure). The video file is uploaded with
  FILE_UPLOAD from the creator's PC only after they press Publish. The app then polls the post status
  and shows it to the creator.»

## 4. Vídeo de demostración (obligatorio)

Hasta 5 vídeos de 50 MB. Debe mostrar el flujo completo con la integración real (sandbox) y todos los
permisos pedidos. Guion propuesto (grábalo con la Grabadora de Windows o OBS, en inglés o con subtítulos):

1. Abrir ClipStream. Pestaña **Fuentes**: pegar el link de un vídeo **propio** de tu canal de Kick
   (kick.com/ermandev7/videos/…) y pulsar **Analizar**. Se ven los pasos con porcentaje y los clips
   generándose de uno en uno (se puede cortar la espera en la edición).
2. Pestaña **Exportar**: panel TikTok → **Conectar con TikTok** → pantalla de autorización de TikTok →
   aceptar → vuelve a ClipStream mostrando avatar y nombre (user.info.basic).
3. Seleccionar un clip → **Publicar en TikTok**. Enseñar la ventana: cuenta de destino, vista previa,
   texto editable, «¿Quién puede ver este vídeo?» sin valor por defecto (elegir una opción), comentarios /
   dúo / stitch desmarcados, interruptor de contenido comercial con «Tu marca» / «Contenido de marca» y
   sus avisos, declaración de Music Usage Confirmation, aviso de procesado.
4. Pulsar **Publicar**: estado «Subiendo → Procesando en TikTok → Publicado» en ClipStream.
5. **Publicar por tandas**: abrir la tanda, enseñar que cada clip tiene su hora, su texto y su casilla
   «Apruebo publicar este clip»; aprobar dos y **Programar** (aparecen como «Programado» en el panel).
6. Abrir el perfil de TikTok y enseñar el vídeo publicado (privado en sandbox).
7. **Desconectar** en ClipStream (revoca el acceso).

Grábalo tú (la Recortes de Windows graba pantalla con Win+Mayús+R), porque el inicio de sesión y la
autorización en TikTok los tienes que hacer tú. Usa contenido grabado por ti: un vídeo de reacción a
vídeos de terceros puede hacer pensar al revisor que la app copia contenido de otras plataformas.

## 5. Auditoría de Content Posting API (para publicar en público)

Tras aprobar la app, solicita la auditoría desde el portal (Content Posting API → audit). Pide
estimaciones de uso (usuarios y publicaciones al día): sé realista. Las guías hablan de 2 a 6 semanas
(no confirmado oficialmente). Lo publicado antes de la auditoría **no** pasa a público después.

## 6. Publicar las páginas legales

`docs/site/` contiene `index.html`, `privacy.html` y `terms.html`, listas para publicar gratis (por
ejemplo con **GitHub Pages**: repositorio público con esa carpeta → Settings → Pages). Antes de
publicarlas, rellena los datos marcados como `[TU NOMBRE]`, `[TU EMAIL DE CONTACTO]` y `[PAÍS]`.

## 7. Qué cumple ya ClipStream (normas de integración)

| Norma de TikTok | En ClipStream |
|---|---|
| Consultar creator_info al abrir la ventana, sin caché | Sí, cada vez que se abre |
| Mostrar el nombre de la cuenta de destino | Sí, con avatar y @usuario |
| Comprobar la duración máxima | Sí, avisa y bloquea el botón |
| Privacidad elegida a mano, sin valor por defecto | Sí («Elige una opción…») |
| Comentar / Dúo / Stitch desmarcados y bloqueados si la cuenta los desactiva | Sí |
| Divulgación comercial con «Tu marca» / «Contenido de marca», avisos y botón desactivado si falta elegir | Sí |
| Contenido de marca no puede ser privado | Sí, «Solo yo» se desactiva |
| Declaración de Music Usage Confirmation / Branded Content Policy | Sí, con enlaces |
| Vista previa del vídeo | Sí |
| Sin marcas de agua ni texto promocional añadido; texto editable | Sí |
| No enviar nada antes del consentimiento | Sí, solo al pulsar Publicar/Programar |
| Aviso de que el procesado tarda unos minutos y estado de la publicación | Sí |
| FILE_UPLOAD para archivos locales, por trozos | Sí |
| Client Secret confidencial | Sí, cifrado con DPAPI en el PC, nunca en el código |
| Límite ~15 publicaciones/día | Sí, ClipStream no deja pasar de 15 en 24 h |

Las publicaciones **programadas** se revisan y aceptan una a una igual que las inmediatas; antes de
subirlas se vuelve a consultar la cuenta. TikTok no menciona la programación en sus normas: si en la
revisión ponen objeciones, se puede desactivar.
