fn main() {
    // Generates Tauri context (parses tauri.conf.json, embeds capabilities and
    // icons). Tauri v2 requires this in the build script of the GUI binary.
    tauri_build::build();
}
