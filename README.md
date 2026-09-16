# Licitaciones Fotovoltaicas — correo + página web (actualiza cada hora)

Revisa cada hora las licitaciones de los últimos 30 días en Mercado Público,
filtra las del rubro fotovoltaico (nombre, descripción e ítems), evalúa
cuáles siguen realmente vigentes, y:

1. Te avisa por correo solo las **nuevas**.
2. Publica una **página web** con TODAS las que están vigentes ahora mismo,
   accesible desde un link fijo, actualizada automáticamente cada hora.

## 1. Sube esta carpeta a un repositorio de GitHub

Puede ser privado (recomendado) o público — para GitHub Pages gratis ambos
funcionan igual. Mantén la estructura de carpetas tal cual está.

⚠️ **No copies tu ticket ni tu contraseña dentro de `buscar_licitaciones.py`.**
El script los lee de variables de entorno / secretos de GitHub — así nunca
quedan escritos en el código.

## 2. Configura los secretos (igual que antes)

**Settings → Secrets and variables → Actions → New repository secret:**

| Secreto | Valor |
|---|---|
| `MP_TICKET` | Tu ticket de acceso a la API. |
| `SMTP_USER` | Tu correo Gmail emisor. |
| `SMTP_PASS` | Contraseña de aplicación de 16 caracteres (no tu clave normal). |
| `EMAIL_TO` | Destinatario(s), separados por coma si son varios. |

## 3. Activa GitHub Pages (esto es lo nuevo)

1. Ve a **Settings → Pages**.
2. En "Build and deployment" → **Source**, elige **"Deploy from a branch"**.
3. En **Branch**, elige `main` y la carpeta **`/docs`**. Guarda.
4. GitHub te va a mostrar el link donde quedará publicada, algo como:
   `https://tu-usuario.github.io/nombre-del-repo/`
   (puede tardar 1-2 minutos en activarse la primera vez).

Ese link es fijo — lo puedes guardar de favorito, mandarlo a tu equipo, o
abrirlo desde el celular en cualquier momento. Cada vez que el workflow
corra (cada hora), la página se actualiza sola.

## 4. Pruébalo ahora mismo

En la pestaña **Actions** de tu repositorio, entra al workflow "Buscar
licitaciones fotovoltaicas (cada hora)" y presiona **Run workflow**. Cuando
termine (1-3 minutos), refresca tu página de GitHub Pages.

## 5. Frecuencia

Por defecto corre **cada hora en punto**. Si prefieres menos frecuencia
(por ejemplo, para generar menos tráfico a la API), edita esta línea en
`.github/workflows/buscar_licitaciones.yml`:

```yaml
- cron: "0 * * * *"   # cada hora
```

Ejemplos: `"0 */3 * * *"` (cada 3 horas), `"0 8,13,18 * * *"` (3 veces al
día, a las 8, 13 y 18 hrs).

## 6. Correr solo la página, sin recibir más correos

Si en algún momento quieres dejar de recibir correos pero seguir viendo la
página actualizada, agrega un secreto extra `ENVIAR_CORREO` con valor
`false`.

## 7. Uso local (tu PC), sin exponer credenciales

1. Copia `ejecutar_local.bat.ejemplo` como `ejecutar_local.bat` (ese nombre
   ya está en `.gitignore`, así que nunca se sube a GitHub aunque hagas
   `git add .`).
2. Completa tus datos reales dentro de ese archivo.
3. Doble clic para correrlo — genera `docs/index.html` localmente también,
   lo puedes abrir directo con el navegador desde tu PC.

## 8. Cómo funciona el filtro

Revisa el nombre, la descripción y los ítems (nombre/categoría de cada
producto o servicio) de cada licitación contra una lista de palabras clave
del rubro fotovoltaico. Se puede editar libremente en
`buscar_licitaciones.py`, en la variable `PALABRAS_CLAVE`.

## 9. Sobre la contraseña que se compartió en el chat

Como tu contraseña de aplicación de Gmail viajó en texto plano en una
conversación anterior, te recomendamos regenerarla cuando puedas (gratis,
1 minuto): https://myaccount.google.com/apppasswords — genera una nueva y
actualiza el secreto `SMTP_PASS` en GitHub con el nuevo valor.
