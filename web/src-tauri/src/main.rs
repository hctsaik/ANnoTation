#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::{
    env,
    error::Error,
    fs::{self, File},
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
};
use tauri::{Manager, State};

struct Backend {
    api_base: String,
    child: Mutex<Child>,
}

impl Backend {
    fn stop(&self) {
        if let Ok(mut child) = self.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[tauri::command]
fn get_api_base(backend: State<'_, Backend>) -> String {
    backend.api_base.clone()
}

fn repository_root() -> Result<PathBuf, Box<dyn Error>> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    Ok(manifest_dir
        .parent()
        .ok_or("missing web directory")?
        .parent()
        .ok_or("missing repository directory")?
        .to_path_buf())
}

fn python_executable() -> Result<PathBuf, Box<dyn Error>> {
    if let Some(python) = env::var_os("ANNOTATION_PYTHON") {
        return Ok(PathBuf::from(python));
    }

    #[cfg(windows)]
    {
        let output = Command::new("py")
            .args(["-3.11-64", "-c", "import sys; print(sys.executable)"])
            .output()?;
        if !output.status.success() {
            return Err("Python 3.11 could not be resolved; set ANNOTATION_PYTHON".into());
        }
        let path = String::from_utf8(output.stdout)?.trim().to_owned();
        if path.is_empty() {
            return Err("Python launcher returned an empty interpreter path".into());
        }
        return Ok(PathBuf::from(path));
    }

    #[cfg(not(windows))]
    Ok(PathBuf::from("python3"))
}

fn start_backend(app: &tauri::AppHandle) -> Result<Backend, Box<dyn Error>> {
    let root = repository_root()?;
    let app_data_dir = app.path().app_data_dir()?;
    fs::create_dir_all(&app_data_dir)?;
    let log_path = app_data_dir.join("annotation-backend.log");
    let stderr = File::create(log_path)?;

    let mut child = Command::new(python_executable()?)
        .args([
            "-m",
            "annotation_web.server",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--app-data-dir",
        ])
        .arg(&app_data_dir)
        .args(["--web-dir"])
        .arg(root.join("web").join("dist"))
        .current_dir(&root)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(stderr))
        .spawn()?;

    let stdout = match child.stdout.take() {
        Some(stdout) => stdout,
        None => {
            let _ = child.kill();
            return Err("backend stdout was not available".into());
        }
    };
    let mut ready_line = String::new();
    if let Err(error) = BufReader::new(stdout).read_line(&mut ready_line) {
        let _ = child.kill();
        return Err(Box::new(error));
    }

    let ready: Value = match serde_json::from_str::<Value>(&ready_line) {
        Ok(value) if value["event"] == "ready" => value,
        Ok(_) => {
            let _ = child.kill();
            return Err("backend did not report ready".into());
        }
        Err(error) => {
            let _ = child.kill();
            return Err(Box::new(error));
        }
    };
    let port = match ready["port"].as_u64() {
        Some(port) => port,
        None => {
            let _ = child.kill();
            return Err("backend returned no port".into());
        }
    };
    Ok(Backend {
        api_base: format!("http://127.0.0.1:{port}"),
        child: Mutex::new(child),
    })
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let backend = start_backend(app.handle())?;
            app.manage(backend);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_api_base])
        .build(tauri::generate_context!())
        .expect("failed to build ANnoTation desktop host");

    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(backend) = handle.try_state::<Backend>() {
                backend.stop();
            }
        }
    });
}
