import React, { useEffect } from 'react';
import { X, Terminal } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useGraphStore } from '../store/graphStore';
import { TerminalLog } from './TerminalLog';
import { cn } from '@/lib/utils';

interface DetailModalProps {
  isOpen: boolean;
  agentId: string | null;
  onClose: () => void;
}

export function DetailModal({ isOpen, agentId, onClose }: DetailModalProps) {
  const node = useGraphStore(state => 
    state.nodes.find(n => n.id === agentId)
  );

  // Close on escape
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  return (
    <AnimatePresence>
      {isOpen && agentId && node && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
          />
          
          {/* Modal */}
          <motion.div
            layoutId={agentId} // Shared layout ID if we could animate from the node
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-4 z-50 m-auto max-w-4xl max-h-[90vh] flex flex-col bg-slate-950 border border-slate-800 shadow-2xl rounded-xl overflow-hidden"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900/50">
              <div className="flex items-center gap-3">
                <div className="p-1.5 bg-blue-500/10 rounded-md">
                    <Terminal className="w-5 h-5 text-blue-400" />
                </div>
                <div>
                    <h2 className="font-bold text-slate-100">{node.data.label}</h2>
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                        <span className="uppercase">{node.data.pattern || 'Agent'}</span>
                        <span>•</span>
                        <span className={cn(
                            "capitalize",
                            node.data.status === 'active' ? "text-emerald-400" :
                            node.data.status === 'failed' ? "text-red-400" : "text-slate-400"
                        )}>{node.data.status}</span>
                    </div>
                </div>
              </div>
              <button 
                onClick={onClose}
                className="p-2 hover:bg-slate-800 rounded-lg transition-colors text-slate-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-hidden flex flex-col bg-black/40">
                {/* We reuse TerminalLog but filtered for this agent */}
                {/* Note: TerminalLog expects LogItem[] */}
                <div className="flex-1 overflow-auto p-4">
                     {node.data.logs && node.data.logs.length > 0 ? (
                         <TerminalLog logs={node.data.logs} />
                     ) : (
                         <div className="flex flex-col items-center justify-center h-full text-slate-500">
                             <p>No logs available for this agent yet.</p>
                         </div>
                     )}
                </div>
            </div>
            
            {/* Footer */}
            <div className="p-3 border-t border-slate-800 bg-slate-900/30 text-xs text-slate-500 font-mono flex justify-between">
                <span>ID: {agentId}</span>
                <span>{node.data.logs?.length || 0} events</span>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
