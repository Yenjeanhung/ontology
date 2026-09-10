# -*- coding: utf-8 -*-
"""临时脚本：清理重复/错误 import 并校验。用完即删。"""
import io
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

FILES = [
    "front/src/components/entity/EntityListPage.vue",
    "front/src/components/ontology/FunctionEditorPage.vue",
    "front/src/components/ontology/ServiceEditorPage.vue",
    "front/src/components/ontology/ActionFlowCanvas.vue",
    "front/src/components/ontology/OntologyEditor.vue",
    "front/src/components/workflow/WorkflowEditorPage.vue",
    "front/src/components/workflow/WorkflowNode.vue",
    "front/src/components/KbList.vue",
    "front/src/components/FileLibrary.vue",
]


def main():
    for rel in FILES:
        path = os.path.join(ROOT, rel)
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
        nl = "\r\n" if "\r\n" in text else "\n"
        lines = text.split(nl)

        # 修正错误路径 ..//
        lines = [l.replace("'..//composables/useEscClose'", "'../composables/useEscClose'") for l in lines]

        # 去重：仅保留首个 useEscClose 的 import
        seen = False
        cleaned = []
        for l in lines:
            if l.startswith("import { useEscClose }"):
                if seen:
                    continue
                seen = True
            cleaned.append(l)

        out = nl.join(cleaned)
        if out != text:
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(out)

        imports = sum(1 for l in cleaned if l.startswith("import { useEscClose }"))
        calls = sum(1 for l in cleaned if l.startswith("useEscClose("))
        status = "OK " if (imports == 1 and calls == 1) else "BAD"
        print("%s %-55s import=%d call=%d" % (status, rel, imports, calls))


if __name__ == "__main__":
    main()
