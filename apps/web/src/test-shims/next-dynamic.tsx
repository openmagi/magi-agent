import { useEffect, useState, type ComponentType } from "react";

type DynamicLoader<TProps extends object> = () => Promise<{ default: ComponentType<TProps> }>;

interface DynamicOptions {
  ssr?: boolean;
  loading?: ComponentType;
}

export default function dynamic<TProps extends object>(
  loader: DynamicLoader<TProps>,
  options: DynamicOptions = {},
): ComponentType<TProps> {
  return function DynamicComponent(props: TProps) {
    const [Component, setComponent] = useState<ComponentType<TProps> | null>(null);

    useEffect(() => {
      let cancelled = false;
      loader().then((mod) => {
        if (!cancelled) setComponent(() => mod.default);
      });
      return () => {
        cancelled = true;
      };
    }, []);

    if (!Component) {
      const Loading = options.loading;
      return Loading ? <Loading /> : null;
    }
    const Loaded = Component;
    return <Loaded {...props} />;
  };
}
