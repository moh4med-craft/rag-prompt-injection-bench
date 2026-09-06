"""Accès au modèle de langage, derrière une interface unique.

Contrainte de conception : le banc doit pouvoir changer de modèle sans qu'aucune
autre ligne du projet ne change. C'est ce qui permet de répondre à la question
« ton taux d'injection dépend-il du modèle ? » — question qu'on posera, et dont la
réponse est oui.

Ollama est le fournisseur par défaut : il tourne en local, sans GPU ni clé d'API,
donc n'importe qui peut reproduire les mesures après un `docker compose up`. Un
point de terminaison compatible OpenAI est accepté en alternative.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from .config import Config


@dataclass(frozen=True)
class Completion:
    text: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Client synchrone. Le banc est séquentiel par choix : on mesure des latences."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.provider = config.llm_provider
        self._client = httpx.Client(timeout=180.0)

    def chat(self, system: str, user: str) -> Completion:
        if self.provider == "ollama":
            return self._ollama(system, user)
        if self.provider == "openai":
            return self._openai(system, user)
        raise LLMError(f"fournisseur inconnu : {self.provider}")

    def _ollama(self, system: str, user: str) -> Completion:
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        payload = {
            "model": self.config.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            # `seed` en plus de `temperature` : à température nulle le décodage est
            # déjà déterministe, mais le seed protège des chemins de repli du serveur.
            "options": {
                "temperature": self.config.temperature,
                "seed": 0,
                "num_predict": self.config.max_tokens,
            },
        }
        r = self._client.post(f"{host}/api/chat", json=payload)
        if r.status_code != 200:
            raise LLMError(f"ollama {r.status_code}: {r.text[:300]}")
        data = r.json()
        return Completion(
            text=data["message"]["content"].strip(),
            latency_ms=int(data.get("total_duration", 0) / 1e6),
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
        )

    def _openai(self, system: str, user: str) -> Completion:
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY absent")
        import time

        t0 = time.perf_counter()
        r = self._client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": self.config.llm_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "seed": 0,
            },
        )
        if r.status_code != 200:
            raise LLMError(f"openai {r.status_code}: {r.text[:300]}")
        data = r.json()
        usage = data.get("usage", {})
        return Completion(
            text=data["choices"][0]["message"]["content"].strip(),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def health(self) -> str:
        if self.provider != "ollama":
            return f"{self.provider}:{self.config.llm_model}"
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        try:
            tags = self._client.get(f"{host}/api/tags", timeout=5.0).json()
        except Exception as exc:  # pragma: no cover - diagnostic
            raise LLMError(f"ollama injoignable sur {host} ({exc})") from exc
        names = [m["name"] for m in tags.get("models", [])]
        if self.config.llm_model not in names:
            raise LLMError(
                f"modèle {self.config.llm_model} absent. Disponibles : {names or 'aucun'}"
            )
        return f"ollama:{self.config.llm_model}"
