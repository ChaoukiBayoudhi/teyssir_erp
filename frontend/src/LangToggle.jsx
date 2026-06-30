import { ToggleButton, ToggleButtonGroup } from "@mui/material";
import { useTranslation } from "react-i18next";

export default function LangToggle() {
  const { i18n } = useTranslation();
  return (
    <ToggleButtonGroup
      size="small"
      exclusive
      value={i18n.language}
      onChange={(_, v) => v && i18n.changeLanguage(v)}
    >
      <ToggleButton value="fr">FR</ToggleButton>
      <ToggleButton value="ar">ع</ToggleButton>
    </ToggleButtonGroup>
  );
}
