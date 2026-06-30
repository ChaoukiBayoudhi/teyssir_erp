import { createTheme } from "@mui/material/styles";
import createCache from "@emotion/cache";
import { prefixer } from "stylis";
import rtlPlugin from "stylis-plugin-rtl";

// Library-green Material theme (spec §11.3), built per text direction.
export function makeTheme(direction) {
  return createTheme({
    direction,
    palette: {
      primary: { main: "#1B5E20" },
      secondary: { main: "#8D6E63" },
      warning: { main: "#F9A825" },
      error: { main: "#C62828" },
    },
    typography: {
      fontFamily:
        direction === "rtl"
          ? "Tajawal, Cairo, 'Noto Naskh Arabic', sans-serif"
          : "Inter, Roboto, system-ui, sans-serif",
    },
  });
}

export const ltrCache = createCache({ key: "mui-ltr", stylisPlugins: [prefixer] });
export const rtlCache = createCache({ key: "mui-rtl", stylisPlugins: [prefixer, rtlPlugin] });
