import React, { useMemo } from 'react';
import yaml from 'js-yaml';
import { Copy, FileText } from 'lucide-react';
import { cn } from '@/lib/utils';

interface YamlViewProps {
  config: any;
  className?: string;
}

export function YamlView({ config, className }: YamlViewProps) {
  const yamlString = useMemo(() => {
    try {
      return yaml.dump(config, {
        indent: 2,
        lineWidth: -1, // Don't wrap lines
        noRefs: true,  // Don't use aliases
      });
    } catch (e) {
      return '# Error converting config to YAML\n' + String(e);
    }
  }, [config]);

  const copyToClipboard = () => {
    navigator.clipboard.writeText(yamlString);
  };

  return (
    <div className={cn("flex flex-col h-full bg-slate-950 text-slate-300 font-mono text-sm relative", className)}>
      <div className="flex items-center justify-between px-4 py-2 border-b border-slate-800 bg-slate-900/50 shrink-0">
        <div className="flex items-center gap-2 text-slate-400">
          <FileText className="w-4 h-4" />
          <span className="text-xs font-bold uppercase tracking-wider">Configuration.yaml</span>
        </div>
        <button 
          onClick={copyToClipboard}
          className="p-1.5 hover:bg-slate-800 rounded text-slate-500 hover:text-slate-300 transition-colors"
          title="Copy to clipboard"
        >
          <Copy className="w-4 h-4" />
        </button>
      </div>
      
      <div className="flex-1 overflow-auto p-4 custom-scrollbar">
        <pre className="text-xs leading-relaxed text-blue-100/90">
          <code>{yamlString}</code>
        </pre>
      </div>
    </div>
  );
}
