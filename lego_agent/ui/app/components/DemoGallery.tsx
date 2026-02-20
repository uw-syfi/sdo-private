import React from 'react';
import { GitGraph, RefreshCcw, Network, Boxes } from 'lucide-react';

interface DemoGalleryProps {
  onSelect: (pattern: string) => void;
}

export const DemoGallery: React.FC<DemoGalleryProps> = ({ onSelect }) => {
  const patterns = [
    {
      id: 'fan_out',
      title: 'Fan Out',
      description: 'One supervisor delegates tasks to multiple workers in parallel, then summarizes their results.',
      icon: Network,
      color: 'text-blue-400',
      bg: 'bg-blue-400/10',
      border: 'border-blue-400/20'
    },
    {
      id: 'judge_loop',
      title: 'Judge Loop',
      description: 'An iterative process where a generator produces output and a critic provides feedback until quality criteria are met.',
      icon: RefreshCcw,
      color: 'text-emerald-400',
      bg: 'bg-emerald-400/10',
      border: 'border-emerald-400/20'
    },
    {
      id: 'hierarchical',
      title: 'Software House',
      description: 'A nested hierarchy of agents: Product Manager -> (Frontend Team + Backend Team).',
      icon: GitGraph,
      color: 'text-purple-400',
      bg: 'bg-purple-400/10',
      border: 'border-purple-400/20'
    },
    {
      id: 'sequential',
      title: 'Sequential Chain',
      description: 'A simple linear pipeline where the output of one agent becomes the input of the next.',
      icon: Boxes,
      color: 'text-orange-400',
      bg: 'bg-orange-400/10',
      border: 'border-orange-400/20'
    }
  ];

  return (
    <div className="w-full h-full flex flex-col items-center justify-center p-8 overflow-y-auto">
      <div className="max-w-4xl w-full space-y-8">
        <div className="text-center space-y-2">
          <h2 className="text-3xl font-bold tracking-tight">Select a Pattern</h2>
          <p className="text-muted-foreground">Choose a multi-agent workflow to verify visualization behavior.</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {patterns.map((pattern) => (
            <button
              key={pattern.id}
              onClick={() => onSelect(pattern.id)}
              className={`
                relative group flex flex-col items-start p-6 text-left rounded-xl border transition-all duration-200
                hover:scale-[1.02] hover:shadow-lg
                ${pattern.bg} ${pattern.border}
              `}
            >
              <div className={`p-3 rounded-lg bg-background/50 mb-4 ${pattern.color}`}>
                <pattern.icon className="w-6 h-6" />
              </div>
              
              <h3 className="text-lg font-semibold mb-2">{pattern.title}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {pattern.description}
              </p>
              
              <div className="absolute inset-0 rounded-xl ring-1 ring-inset ring-foreground/5 opacity-0 group-hover:opacity-100 transition-opacity" />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
