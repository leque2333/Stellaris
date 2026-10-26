import re


class ContinuationPredictor:
    def __init__(self, config=None):
        config = config or {}
        self.enabled = bool(config.get("enable_dynamic_debounce", True))
        self.base_delay = float(config.get("debounce_time", 2.0))
        self.min_delay = float(config.get("min_debounce_time", 1.2))
        self.max_delay = float(config.get("max_debounce_time", 8.0))
        self.extension = float(config.get("continuation_extension", 4.0))
        self.fast_complete_comma_count = int(config.get("fast_complete_comma_count", 2))
        self.fast_complete_min_chars = int(config.get("fast_complete_min_chars", 30))
        self.fast_complete_delay = float(
            config.get("fast_complete_delay", self.min_delay)
        )

        self.strong_signals = {
            "等下", "等等", "等会", "还有", "然后", "对了", "不是", "刚刚",
            "我再发", "再发", "看图", "看这个", "补充", "另外", "顺便",
            "还有个", "还有一个", "别急", "先别回", "先别说", "我继续",
            "接着", "下一张", "上一张", "这个图", "表情包",
        }
        self.open_endings = {
            "，", ",", "、", "：", ":", "；", ";", "…", "......", "...",
            "然后", "还有", "以及", "或者", "但是", "不过", "因为", "所以",
            "就是", "比如", "例如", "还有就是",
        }
        self.close_signals = {
            "吗", "么", "？", "?", "怎么做", "怎么办", "帮我", "解释一下",
            "总结", "分析", "看看", "可以吗", "行吗", "回复我", "说说",
        }

    def _has_strong_continuation_signal(self, text: str) -> bool:
        lowered = text.lower()
        return (
            any(sig in text for sig in self.strong_signals)
            or bool(re.search(r"(先|等).*?(别|不要).*?(回|说|答)", lowered))
            or bool(re.search(r"(第[一二三四五六七八九十\d]+张|下一张|上一张)", text))
        )

    def _looks_fast_complete(self, text: str, has_image: bool) -> bool:
        if has_image:
            return False
        stripped = (text or "").strip()
        if len(stripped) <= self.fast_complete_min_chars:
            return False
        comma_count = stripped.count(",") + stripped.count("，")
        if comma_count < self.fast_complete_comma_count:
            return False
        if self._looks_open_ended(stripped):
            return False
        return not self._has_strong_continuation_signal(stripped)

    def _looks_open_ended(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        return any(stripped.endswith(s) for s in self.open_endings)

    def probability(self, text: str, has_image: bool = False, item_count: int = 1) -> float:
        if not self.enabled:
            return 0.0
        text = (text or "").strip()
        score = 0.0
        if has_image:
            score += 0.22
        if not text and has_image:
            score += 0.35
        if text:
            lowered = text.lower()
            if len(text) <= 4:
                score += 0.18
            elif len(text) <= 12:
                score += 0.10
            if self._looks_open_ended(text):
                score += 0.30
            if text.endswith(("。", "！", "!", "？", "?")):
                score -= 0.12
            if any(sig in text for sig in self.strong_signals):
                score += 0.34
            if any(sig in text for sig in self.close_signals):
                score -= 0.18
            if re.search(r"(先|等).*?(别|不要).*?(回|说|答)", lowered):
                score += 0.45
            if re.search(r"(第[一二三四五六七八九十\d]+张|下一张|上一张)", text):
                score += 0.25
            if self._looks_fast_complete(text, has_image):
                score = min(score, 0.02)
        if item_count > 1:
            score += min(0.10, item_count * 0.025)
        return max(0.0, min(score, 1.0))

    def delay(self, text: str, has_image: bool = False, item_count: int = 1) -> float:
        if not self.enabled:
            return self.base_delay
        if self._looks_fast_complete(text, has_image):
            return round(max(self.min_delay, min(self.max_delay, self.fast_complete_delay)), 2)
        probability = self.probability(text, has_image=has_image, item_count=item_count)
        delay = self.base_delay + probability * self.extension
        delay = max(self.min_delay, min(self.max_delay, delay))
        return round(delay, 2)
