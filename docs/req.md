
no multistage build

use separeted folder/file for styling.
use bootstrap.

format file list as file explorer, show the files properties as a table.
display the allowd extensions on the index.

Web App

Will have an 10Gb upload limit (per file)

One panel with an List of files that user can download

one button to user upload files (10Gb limit)

Files will be erased every 24 hours

## constrains:

- Run on Docker
- Run on port 3010 expose to 0.0.0.0
- UI: simple but elegant UI
- shell script to gen key.
- Add drag and drop area to upload files

## Feature
add the same concept to share clipboard. Clipboard will be another section will display cards with the pasted text with the copy icon/button. If image is present at clipboard, will be saved as file and will have an clipboard as part of it name. Clipboard text will also be available to download as files  
On text clipboard add button to enable see the content of the file, like open an acordeon.

at the clipboard section now we have three butons: "View Content", "Download" and "Copy". downlaod and copy are aligned, but view content is slightly higher. Also "View Content" is actualy showing the final file name of the clipboard and not the actual text of the texual clipboard.  
