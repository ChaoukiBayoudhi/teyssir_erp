from django.core.management.base import BaseCommand

from teyssir.core.llm import generate, llm_enabled, llm_model, ollama_reachable, status


class Command(BaseCommand):
    help = "Check local Ollama LLM (optional). Exit 0 even when AI is off unless --strict."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true",
                            help="Exit 1 if LLM is enabled but unreachable")
        parser.add_argument("--ping", action="store_true", help="Probe Ollama /api/tags")
        parser.add_argument("--prompt", default="", help="Optional generate() smoke test")

    def handle(self, *args, **opts):
        info = status(ping=opts["ping"])
        self.stdout.write(str(info))
        if opts["ping"] and not info.get("reachable"):
            self.stdout.write(self.style.WARNING("Ollama API not reachable — ERP still works."))
            if opts["strict"] and llm_enabled():
                raise SystemExit(1)
            return
        prompt = (opts["prompt"] or "").strip()
        if prompt:
            if not ollama_reachable():
                self.stdout.write(self.style.WARNING("Skip prompt: Ollama down."))
                return
            reply = generate(prompt)
            self.stdout.write(reply or "(empty reply)")
        self.stdout.write(self.style.SUCCESS(f"LLM config model={llm_model()} enabled={llm_enabled()}"))
