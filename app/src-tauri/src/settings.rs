//! User settings, persisted as JSON in the app config directory.

use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default)]
pub struct Settings {
    /// How the learner's voice becomes text: audio (Gemma), whisper, kyutai.
    pub stt: String,
    /// How a turn ends: ptt, vad, semantic.
    pub turn: String,
    /// Tutor voice: kokoro, kyutai.
    pub tts: String,
    /// Playback speed of the tutor (UI range 0.75 - 1.25).
    pub speed: f64,
    /// VAD mode: seconds of silence that end the turn.
    pub silence: f64,
    /// Program text: fr or en. The tutor always speaks French.
    pub ui_lang: String,
    /// Translate and extract vocabulary in the background after each turn.
    pub prefetch: bool,
    /// Speak the greeting when the engine is ready.
    pub greet: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            stt: "audio".into(),
            turn: "ptt".into(),
            tts: "kokoro".into(),
            speed: 0.92,
            silence: 1.2,
            ui_lang: "fr".into(),
            prefetch: true,
            greet: true,
        }
    }
}

impl Settings {
    pub fn load(path: &Path) -> Self {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|s| serde_json::from_str::<Settings>(&s).ok())
            .unwrap_or_default()
            .sanitized()
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let json = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        std::fs::write(path, json).map_err(|e| e.to_string())
    }

    /// Clamp values. Memory limits come from the engine (Metal's recommended
    /// working set), so they are enforced in the UI, not here.
    pub fn sanitized(mut self) -> Self {
        let d = Settings::default();
        if !["audio", "whisper", "kyutai"].contains(&self.stt.as_str()) {
            self.stt = d.stt.clone();
        }
        if !["ptt", "vad", "semantic"].contains(&self.turn.as_str()) {
            self.turn = d.turn.clone();
        }
        if !["kokoro", "kyutai"].contains(&self.tts.as_str()) {
            self.tts = d.tts.clone();
        }
        if !["fr", "en"].contains(&self.ui_lang.as_str()) {
            self.ui_lang = d.ui_lang.clone();
        }
        self.speed = self.speed.clamp(0.75, 1.25);
        self.silence = self.silence.clamp(0.6, 3.0);
        self
    }

    /// True if going from `self` to `other` needs a sidecar restart (models
    /// change). Turn mode and speed are changed live.
    pub fn needs_restart(&self, other: &Settings) -> bool {
        self.stt != other.stt
            || self.tts != other.tts
            || self.silence != other.silence
            || self.prefetch != other.prefetch
    }

    pub fn engine_args(&self) -> Vec<String> {
        let mut args = vec![
            "--stt".into(),
            self.stt.clone(),
            "--turn".into(),
            self.turn.clone(),
            "--tts".into(),
            self.tts.clone(),
            "--speed".into(),
            format!("{:.2}", self.speed),
            "--silence".into(),
            format!("{:.2}", self.silence),
        ];
        if !self.prefetch {
            args.push("--no-prefetch".into());
        }
        if !self.greet {
            args.push("--no-greet".into());
        }
        args
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_any_model_combination() {
        let s = Settings { turn: "semantic".into(), tts: "kyutai".into(), ..Default::default() }.sanitized();
        assert_eq!(s.tts, "kyutai");
        let s = Settings { stt: "bogus".into(), ..Default::default() }.sanitized();
        assert_eq!(s.stt, "audio");
    }

    #[test]
    fn clamps_and_restart_rules() {
        let s = Settings { speed: 3.0, ..Default::default() }.sanitized();
        assert_eq!(s.speed, 1.25);
        let a = Settings::default();
        let b = Settings { turn: "vad".into(), speed: 0.8, ..Default::default() };
        assert!(!a.needs_restart(&b));
        let c = Settings { stt: "whisper".into(), ..Default::default() };
        assert!(a.needs_restart(&c));
    }
}
