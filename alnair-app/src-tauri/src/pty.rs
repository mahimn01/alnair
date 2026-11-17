use portable_pty::{native_pty_system, CommandBuilder, PtySize};
use std::io::{Read, Write};
pub struct Session {
    pub master: Box<dyn portable_pty::MasterPty + Send>,
    pub writer: Box<dyn Write + Send>,
    pub child: Box<dyn portable_pty::Child + Send + Sync>,
    pub run_id: String,
    pub ledger_id: String,
    pub run_path: std::path::PathBuf,
    pub config_dir: std::path::PathBuf,
}
impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_dir_all(&self.config_dir);
    }
}
pub fn size(cols: u16, rows: u16) -> Result<PtySize, String> {
    if !(10..=500).contains(&cols) || !(2..=300).contains(&rows) {
        return Err("Terminal dimensions out of bounds".into());
    }
    Ok(PtySize {
        rows,
        cols,
        pixel_width: 0,
        pixel_height: 0,
    })
}
pub fn spawn(
    command: CommandBuilder,
    dimensions: PtySize,
) -> Result<
    (
        Box<dyn portable_pty::MasterPty + Send>,
        Box<dyn Write + Send>,
        Box<dyn portable_pty::Child + Send + Sync>,
        Box<dyn Read + Send>,
    ),
    String,
> {
    let pair = native_pty_system()
        .openpty(dimensions)
        .map_err(|e| e.to_string())?;
    let child = pair
        .slave
        .spawn_command(command)
        .map_err(|e| e.to_string())?;
    drop(pair.slave);
    let reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
    let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
    Ok((pair.master, writer, child, reader))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_invalid_dimensions() {
        assert!(size(0, 20).is_err());
        assert!(size(80, 24).is_ok())
    }
    #[test]
    fn real_pty_echoes_without_a_model() {
        let mut cmd = CommandBuilder::new("/bin/sh");
        cmd.args(["-c", "printf ALNAIR_PTY_OK"]);
        let (master, _writer, mut child, mut reader) = spawn(cmd, size(80, 24).unwrap()).unwrap();
        let mut text = String::new();
        let _ = reader.read_to_string(&mut text);
        assert!(text.contains("ALNAIR_PTY_OK"));
        let _ = child.wait();
        drop(master);
    }
}
