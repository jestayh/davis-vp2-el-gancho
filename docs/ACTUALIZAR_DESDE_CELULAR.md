# Actualizar la estación desde un celular Android

Toma unos 10 minutos. No se puede romper nada: si algo falla, la estación sigue
funcionando igual que antes.

## Lo que se necesita

- Un celular **Android** (con iPhone no funciona).
- Un **adaptador OTG USB-C → USB-A**: es un adaptador chico; por un lado entra en el
  celular y por el otro tiene un puerto USB "grande" (rectangular). A veces viene
  en la caja del celular; si no, se vende en tiendas de celulares o electrónica.
- El **cable que ya usa la estación** (el que va desde la plaquita al router).

## 1. Instalar la aplicación (una sola vez)

1. En Play Store, buscar e instalar **"Serial USB Terminal"** (de *Kai Morich*, ícono negro con un enchufe).
2. Abrirla, tocar el menú **☰ → Settings** y en **Baud rate** elegir **115200**.
3. En la misma pantalla, en **Send → Newline**, elegir **CR**.
4. Volver atrás. Abajo hay unos botones **M1, M2…**. Mantener presionado **M1** y completar:
   - Name: `Ctrl+C`
   - Value: `03`
   - Mode: **HEX**
   - Tocar **OK**.

## 2. Conectar la estación al celular

1. **Desenchufar del router** el cable de la estación (el extremo grande, que va en el router).
2. Enchufar ese extremo en el **adaptador OTG**, y el adaptador en el **celular**.
   La plaquita se enciende con la energía del celular.
3. Si el celular pregunta *"¿Permitir que Serial USB Terminal acceda al dispositivo USB?"*,
   tocar **Aceptar**.
4. En la aplicación, tocar el ícono del **enchufe** (arriba) para conectar.
   Empezarán a aparecer líneas de texto de la estación.

## 3. Detener el programa

Esperar **1 minuto** a que la estación termine de arrancar, y tocar el botón
**Ctrl+C** (el M1) **2 o 3 veces**, hasta que aparezca una línea que dice solo:

```
>>>
```

## 4. Pegar la línea de actualización

1. Copiar la línea que te envían por WhatsApp (empieza con `import gc`).
2. Pegarla en el campo de texto de abajo de la aplicación y tocar **enviar** (la flecha ➤).
3. Aparecerán mensajes como estos:

```
=== INSTALADOR DE EMERGENCIA ===
Version disponible: v1.0.3 (7 archivos)
[1/7] Descargando app.mpy...
   OK (7506 bytes, huella verificada)
...
=== LISTO: v1.0.3 instalada. Reiniciando en 3 segundos... ===
```

Cuando diga **LISTO**, la estación se reinicia sola. Esperar **1 minuto**.

## 5. Volver a dejarla como estaba

1. Desconectar el adaptador del celular.
2. Enchufar el cable de la estación **de nuevo en el router**.

¡Listo! 🎉

## Si algo no funciona

- **No aparece texto al conectar:** revisar que Baud rate sea **115200**, desconectar y volver a conectar.
- **No aparece `>>>`:** tocar **Ctrl+C** algunas veces más.
- **Aparece un mensaje de ERROR:** sacar una **captura de pantalla** y enviarla. La estación
  sigue con la versión anterior, funcionando normal. Volver a enchufarla en el router.
