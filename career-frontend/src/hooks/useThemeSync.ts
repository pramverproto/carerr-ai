import { useEffect } from 'react';
import { useLayoutStore } from '@/store/layoutStore';

export function useThemeSync() {
  const theme = useLayoutStore((s) => s.theme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
  }, [theme]);

  return theme;
}
