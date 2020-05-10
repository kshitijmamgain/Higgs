import random
from timeit import default_timer as timer
import csv
import optuna
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import (auc, accuracy_score, roc_auc_score, roc_curve, confusion_matrix, 
                             precision_recall_curve, classification_report)
from hyperopt import STATUS_OK, STATUS_FAIL, hp, tpe, Trials, fmin
import matplotlib.pyplot as plt
import catboost as cb
from catboost import CatBoost
import shap
#from google.colab import files



#GLOBAL HYPEROPT PARAMETERS
NUM_EVALS = 5 #number of hyperopt evaluation rounds
N_FOLDS = 5 #number of cross-validation folds on data in each evaluation round
MAX_EVALS = 5

#CATBOOST PARAMETERS
CB_MAX_DEPTH = 16 #maximum tree depth in CatBoost
OBJECTIVE_CB_REG = 'MAE' #CatBoost regression metric
OBJECTIVE_CB_CLASS = 'Logloss' #CatBoost classification metric
NUM_BOOST_ROUNDS = 100
EARLY_STOPPING_ROUNDS = 25
SEED = 47




# random search
PARAM_GRID = {
    'l2_leaf_reg': list(range( 0, 2, 1)),
    'learning_rate' : list(np.logspace(np.log(1e-3), np.log(5e-1), base=np.exp(1), num=1000)),
    'depth': list(range(1,CB_MAX_DEPTH,1)),
    'loss_function': ['Logloss'],
    'border_count': list(range( 32, 128, 1)),
    'bootstrap_type': ['Bayesian', 'Bernoulli'],
    'grow_policy': ['SymmetricTree', 'Depthwise', 'Lossguide'], 
    'custom_loss' : ['AUC','F1','TotalF1','CrossEntropy','Logloss'],
    'score_function': ['Cosine','L2'],
    'eval_metric': ['AUC'],
    'min_data_in_leaf': list(range(1, 50, 1)),
    'od_type': ['IncToDec', 'Iter'],
    'task_type' : ['CPU'],
    'leaf_estimation_backtracking': ['No', 'AnyImprovement']

   
                              
    # 'num_leaves': list(range(16, 196, 4)),
    # 'max_bin': [254],
    # 'lambda_l1': list(np.linspace(0, 1)),
    # 'lambda_l2': list(np.linspace(0, 1)),
    # 'min_data_in_leaf' : list(range(20, 500, 10)),
    # 'class_weight': [None, 'balanced'],
   
    #'eval_metric': ['AUC'],
    # 'boosting_type': ['gbdt', 'goss'],
    
    # 'learning_rate': [0.03, 0.1]
    # 'feature_fraction': list(np.linspace(0.4, 1.0)),
    # 'subsample_for_bin': list(range(20000, 300000, 20000)),
    # 'bagging_freq': list(range(1, 7)),
    # 'verbosity' : [0],
}


# Hyperopt Space
H_SPACE = {
    'l2_leaf_reg': hp.qloguniform('l2_leaf_reg', 0, 2, 1),
    'learning_rate': hp.uniform('learning_rate', 1e-3, 5e-1),
    'depth': hp.quniform('depth', 1, CB_MAX_DEPTH, 1),
    'loss_function': hp.choice('loss_function', ['Logloss']), # RMSE and #MAE and Poisson for regression
    'border_count': hp.quniform('border_count', 32, 128, 1),
    'bootstrap_type': hp.choice('bootstrap_type', 
                                [{'bootstrap_type': 'Bayesian', 
                                  'bagging_temperature': hp.loguniform('bagging_temperature', np.log(1), np.log(50))},
                                 {'bootstrap_type': 'Bernoulli'}]),
    'grow_policy': hp.choice('grow_policy', 
                            [{'grow_policy': 'SymmetricTree'}, {'grow_policy': 'Depthwise'},
                             {'grow_policy': 'Lossguide', 
                              'max_leaves': hp.quniform('max_leaves', 2, 32, 1)}]),

    'custom_loss' : hp.choice('custom_loss', ['AUC','F1','TotalF1','CrossEntropy','Logloss']),
    # The score type used to select the next split during the tree construction
    'score_function': hp.choice('score_function', ['Cosine','L2']),
    # Eval_metric helps to detect overfitting
    'eval_metric': hp.choice('eval_metric', ['AUC']),
    'min_data_in_leaf': hp.quniform('min_data_in_leaf', 1, 50, 1),
    #'random_strength': hp.loguniform('random_strength', np.log(0.005), np.log(5)),
    #'rsm': hp.uniform('rsm', 0.1, 1),
    # RSM :Random subspace method. The percentage of features to use at each split selection
    'od_type': hp.choice('od_type', ['IncToDec', 'Iter']),
    'task_type' : 'CPU',
    'leaf_estimation_backtracking': hp.choice('leaf_estimation_backtracking', ['No', 'AnyImprovement'])
    #'boosting_type':hp.choice('boosting_type',['Ordered','Plain']),
    #boosting_type depends on symmetricality of the grow_policy
    #Ordered for small dataset requiring high accuracy, Plain for large data sets requiring processing speed
    }


class Ctbclass():
    '''Catboost Class applying Hyperopt and Optuna techniques '''
    iteration = 0
    def __init__(self, x_train, y_train, optimization_method, lossguide_verifier = False , GPU = True):
        '''Initializes Catboost Train dataset object
        Parameters
        ----------
        x_train: train data
        y_train: label data
        switch: GPU processing Vs CPU'''
        self.GPU = GPU
        #self.switch = switch
        self.x_train = x_train
        self.y_train = y_train
        self.optimization_method = optimization_method
        
        
        self.lossguide_verifier = lossguide_verifier
        
        #self.optuna_results = pd.DataFrame(columns=)
        self.train_set = cb.Pool(self.x_train, self.y_train)
        
        
    def ctb_crossval(self, params, optim_type):
        '''catboost cross validation model
        Paramters
        ---------
        params: Hyper parameters in dict type from different optimization methods
        optim_type: choose among Optuna, Hyperopt, RandomSearch
        Returns
        ------
        Loss, params, n_estimator, run_time'''
        # initializing the timer
         
        start = timer()
        # if optim_type == 'Optuna':
        #     cv_results = cb.cv(self.train_set, params, fold_count=N_FOLDS, num_boost_round=NUM_BOOST_ROUNDS,
        #                    early_stopping_rounds=EARLY_STOPPING_ROUNDS, stratified=True, partition_random_seed=SEED,
        #                    plot=True)
        # else:
        
        cv_results = cb.cv(self.train_set, params, fold_count=N_FOLDS,
                           num_boost_round=NUM_BOOST_ROUNDS,
                           early_stopping_rounds=EARLY_STOPPING_ROUNDS,
                           stratified=True, partition_random_seed=SEED,
                           plot=True)
        # store the runtime
        run_time = timer() - start
        # Extract the best score
        best_score = np.max(cv_results['test-AUC-mean'])
        # Loss must be minimized
        loss = 1 - best_score
        # Boosting rounds that returned the highest cv score
        n_estimators = int(np.argmax(cv_results['test-AUC-mean']) + 1)
        self.estimator = n_estimators
        print(params)
        return loss, params, n_estimators, run_time
    def train(self):
        if self.optimization_method == 'hyperopt':
            return self.hyperopt_space()
        if self.optimization_method == 'optuna':
            return self.optuna_space()
        if self.optimization_method == 'random_search':
            return self.random_space()
    def hyperopt_space(self):
        '''A method to call the hyperopt optimization
        Parameters
        ----------
        fn_name: is the objective function to minimize defined within the class function
        space: is the hypeorpt space provided as dictionary 
        algo: is the type of search algorithm
        trials: Hyperopt base trials object
        Returns
        -------
        result: best parameter that minimizes the fn_name over max_evals = MAX_EVALS 
        trials: the database in which to store all the point evaluations of the search'''
        fn_name, space, algo, trials='hyperopt_obj', H_SPACE, tpe.suggest, Trials()
        
        # score_function_list0=hp.choice('score_function',['L2', 'Cosine'])
        # score_function_list1=hp.choice('score_function',['L2', 'SolarL2', 'LOOL2', 'NewtonL2'])
        # score_function_list2=hp.choice('score_function',['L2', 'SolarL2', 'LOOL2', 'NewtonL2', 'Cosine'])
        if self.GPU == False:
            space.update({'rsm': hp.uniform('rsm', 0.1, 1),
                          'random_strength': hp.loguniform('random_strength', 
                                                           np.log(0.005), np.log(5))})
        if self.GPU == True:
            space.update({'leaf_estimation_backtracking' : hp.choice ('leaf_estimation_backtracking',['Armijo', 'No', 'AnyImprovement'])})
            
        # else:
        #     space.update({'score_function': hp.choice('score_function',
        #                                                ['L2', 'SolarL2', 'LOOL2', 'NewtonL2','Cosine']),'thread_count': 2})
            

        if (self.lossguide_verifier == True) and (self.GPU == True):
            space.update({'score_function': hp.choice('score_function',['L2', 'SolarL2', 'LOOL2', 'NewtonL2']),'thread_count': 2})
        if (self.lossguide_verifier == False) and (self.GPU == True):
            space.update({'score_function': hp.choice('score_function',['Cosine', 'L2', 'SolarL2', 'LOOL2', 'NewtonL2']),'thread_count': 2})


        fn = getattr(self, fn_name)
        try:
            result = fmin(fn=fn, space= space, algo= algo, max_evals= MAX_EVALS,
                          trials= trials, rstate= np.random.RandomState(SEED))
        except Exception as e:
            return {'status': STATUS_FAIL, 'exception': str(e)}
        self.params = trials.best_trial['result']['params']
        self.params['n_estimators'] = self.estimator
        print(result, trials)
        return trials,self.params
    
    def hyperopt_obj(self, params):
        """Objective function for Gradient Boosting Machine Hyperparameter Optimization"""
        optim_type = 'Hyperopt'
        #space = H_SPACE
        self.iteration += 1
        # Extract the bootstrap_type
        if self.GPU == True:
            params['task_type'] = 'GPU'
        if params['bootstrap_type']['bootstrap_type'] == 'Bayesian':
            #print(params['bootstrap_type'])
            params['bagging_temperature'] = params['bootstrap_type']['bagging_temperature']
            #print(params['bagging_temperature'])
            params['bootstrap_type'] = params['bootstrap_type']['bootstrap_type']
            #print(params['bootstrap_type'])
        else:
            params['bootstrap_type'] = params['bootstrap_type']['bootstrap_type']
            #print(params['bootstrap_type'])
        if params['grow_policy']['grow_policy'] == 'Lossguide':
            #self.veifier = True
            params['max_leaves'] = params['grow_policy']['max_leaves']
            #print(params['max_leaves'])
            params['grow_policy'] = params['grow_policy']['grow_policy']
            if self.GPU == False:
              params['score_function'] = 'L2'
            else:
              self.lossguide_verifier =True
              #params['score_function'] = ['Cosine','L2']
               # ['L2', 'SolarL2', 'LOOL2', 'NewtonL2']
            #     # space.update({'score_function': hp.choice('score_function',
            #                                            ['L2', 'SolarL2', 'LOOL2', 'NewtonL2'])})
            
                
            
  
        else:
            params['grow_policy'] = params['grow_policy']['grow_policy']
            print(params['grow_policy'])

        if self.GPU == False:
            params['taks_type'] = 'CPU'

        # for parameter_name in ['l2_leaf_reg', 'depth', 'border_count']:
        #     params[parameter_name] = int(params[parameter_name])
        
        
        # Perform n_folds cross validation
        loss, params, n_estimators, run_time = self.ctb_crossval(params, optim_type)

        # Dictionary with information for evaluation
        return {'loss':loss, 'params':params, 'iteration':self.iteration,
                'estimators':n_estimators, 'train_time':run_time, 'status':STATUS_OK}

    def optuna_space(self):
        '''Optuna search space'''
        fn_name = 'optuna_obj'
        fn = getattr(self, fn_name)
        try:
            study = optuna.create_study(direction='minimize', 
                                        sampler = optuna.samplers.TPESampler(seed=SEED))
            study.optimize(fn, n_trials = MAX_EVALS)
        except Exception as e:
            return {'exception': str(e)}
        self.params = study.best_params
        self.params['n_estimators'] = self.estimator
        return study

    def optuna_obj(self, trial):
        '''Defining the parameters space inside the function for optuna optimization'''
        if self.GPU == False:
          list_score_function = ['Cosine', 'L2']
          list_task_type = ['CPU']
          list_leaf_estimation_backtracking = ['No', 'AnyImprovement']
          list_grow_policy = ['SymmetricTree', 'Depthwise','Lossguide']
        else:
          list_score_function = ['L2', 'SolarL2', 'LOOL2', 'NewtonL2', 'Cosine']
          list_task_type = ['GPU']
          list_leaf_estimation_backtracking = ['Armijo', 'No', 'AnyImprovement']
          list_grow_policy = ['SymmetricTree', 'Depthwise', 'Lossguide']
        params={
        'l2_leaf_reg': trial.suggest_int('l2_leaf_reg', 0, 2, 1),
        'learning_rate': trial.suggest_uniform('learning_rate', 1e-3, 5e-1),
        'depth': trial.suggest_int('depth', 1, CB_MAX_DEPTH, 1),
        'loss_function': trial.suggest_categorical('loss_function', 
                                                   ['Logloss', 'CrossEntropy']),
        'border_count': trial.suggest_int('border_count', 32, 255, 1),
        'bootstrap_type': trial.suggest_categorical('bootstrap_type',
                                                    ['Bayesian', 'Bernoulli']), 
        'grow_policy': trial.suggest_categorical('grow_policy', list_grow_policy),
        'score_function': trial.suggest_categorical('score_function', list_score_function),
        #'score_function': 'Cosine',
        'eval_metric': trial.suggest_categorical('eval_metric', ['AUC']),
        #'eval_metric': trial.suggest_categorical('eval_metric', ['F1', 'AUC']),
        'min_data_in_leaf': trial.suggest_int('min_data_in_leaf', 1, 50, 1),
        'od_type': trial.suggest_categorical('od_type', ['IncToDec', 'Iter']),
        'task_type': trial.suggest_categorical('task_type',list_task_type),
        'leaf_estimation_backtracking': trial.suggest_categorical('leaf_estimation_backtracking', 
                                                                  list_leaf_estimation_backtracking)
        }

        optim_type = 'Optuna'
        self.iteration += 1
        if self.GPU == False:
          params['random_strength'] = trial.suggest_uniform('random_strength', 
                                                            np.log(0.005), np.log(5))
          params['rsm'] = trial.suggest_uniform('rsm', 0.1, 1)

        if params['grow_policy'] == 'Lossguide':
          params['max_leaves'] = trial.suggest_int('max_leaves', 2, 32)
          
        if params['grow_policy'] == 'Lossguide' and self.GPU == False:
          list_score_function = ['L2']

        if params['grow_policy'] == 'Lossguide' and self.GPU == True:
          list_score_function = ['L2', 'NewtonL2','SolarL2', 'LOOL2' ]
          
        if params['bootstrap_type'] == 'Bayesian':
          params['bagging_temperature'] = trial.suggest_uniform('bagging_temperature',
                                                                np.log(1), np.log(50))
          

        # if params['grow_policy'] == 'Lossguide' and params['task_type'] == 'GPU':
        #   params['score_function'] = trial.suggest_categorical('score_function',['L2','SolarL2','LOOL2','NewtonL2'])
        # elif params['grow_policy'] == 'Lossguide' and params['task_type'] == 'CPU':
        #   params['score_function'] = trial.suggest_categorical('score_function',['L2'])
        # elif params['grow_policy'] != 'Lossguide' and params['task_type'] == 'GPU':
        #   params['score_function'] = trial.suggest_categorical('score_function',['L2','SolarL2','LOOL2','NewtonL2','Cosine'])
        # else:
        #   params['score_function'] = trial.suggest_categorical('score_function',['Cosine','L2'])
            
        #for parameter_name in ['l2_leaf_reg', 'depth', 'border_count']:
         #   params[parameter_name] = int(params[parameter_name])
        
        loss, params, _, _ = self.ctb_crossval(params, optim_type)
        print(params)

        return loss


    def random_space(self):
        '''Random search space'''
        print('Running {} rounds of CatBoost parameter optimisation using Random Search:'.format(MAX_EVALS))
        # Dataframe to hold cv results
        space = PARAM_GRID
        random_results = pd.DataFrame(columns=['loss', 'params', 'iteration', 'estimators',
                                               'time'], index=list(range(MAX_EVALS)))
        
        if self.GPU == False:
            space.update({'rsm' : list(np.linspace(0.1, 1.0)),
                          'random_strength': list(np.logspace(np.log(0.005), np.log(5), base=np.exp(1), num=1000))})
            
        if self.GPU == True:
            space.update({'leaf_estimation_backtracking' : ['Armijo', 'No', 'AnyImprovement'],'thread_count' :[2]})
            

          
        if (self.lossguide_verifier == True) and (self.GPU == True):
             space.update({'score_function': ['L2', 'SolarL2', 'LOOL2', 'NewtonL2']})
        if (self.lossguide_verifier == False) and (self.GPU == True):
             space.update({'score_function': ['Cosine', 'L2', 'SolarL2', 'LOOL2', 'NewtonL2']})



        # Iterate through the specified number of evaluations
        for i in range(MAX_EVALS):

            # Randomly sample parameters for catboost
            params = {key: random.sample(value, 1)[0] for key, value in space.items()}
            results_list = self.randomsrch_obj(params, i)

            # Add results to next row in dataframe
            random_results.loc[i, :] = results_list
        #sort values by the loss
        random_results.sort_values('loss', ascending = True, inplace = True)
        self.params = random_results.loc[0, 'params']
        self.params['n_estimators'] = self.estimator
        return random_results

    def randomsrch_obj(self, params, iteration):
        """Random search objective function. Takes in hyperparameters and returns a list
        of results to be saved."""
        optim_type = 'Random'
        self.iteration += 1
        random.seed(SEED) ##For True Randomized Search deactivate the fixated SEED

        if self.GPU == True:
            params['task_type'] = 'GPU'
        
        if self.GPU == False:
            params['task_type'] = 'CPU'
        
        

        bagging_temperature_dist = list(np.logspace(np.log(1), np.log(50), base=np.exp(1), num=1000))
        if params['bootstrap_type'] == 'Bayesian':
            params['bagging_temperature'] = random.sample(bagging_temperature_dist,1)[0]

        max_leaves_dist = list(range( 2, 32, 1))
        if params['grow_policy'] == 'Lossguide':
            params['max_leaves'] = random.sample(max_leaves_dist,1)[0] 
            if self.GPU == False:
                params['score_function'] = 'L2'
            else:
                self.lossguide_verifier = True

        # Perform n_folds cross validation

        loss, params, n_estimators, run_time = self.ctb_crossval(params, optim_type)

        # Return list of results
        return [loss, params,iteration, n_estimators, run_time]


    def test(self, x_test, y_test):
        """This function evaluates the model on paramters and estimators
        Parameters
        ----------
        x_test: test set; y_test: test label"""
        self.train()
        self.test_set = cb.Pool(x_test, y_test)
        self.cat = cb.train(params=self.params, pool=self.train_set)
        self.pred = self.cat.predict(x_test,prediction_type="Class")
        self.test_y = y_test
        self.test_x = x_test
        print("Model will be trained with best parameters obtained from your choice of optimization model ... \n\n\n")
        print("Model trained with {} estimators on the following parameters: \n{}".format(self.estimator, self.params))

    def shap_summary(self):
        x_test=self.test_x
        z=shap.sample(x_test,nsamples = 100)
        explainer=shap.KernelExplainer(self.cat.predict,z)
        k_shap_values = explainer.shap_values(x_test)
        print("Shap Summary Plot")
        plt.figure()
        shap.summary_plot(k_shap_values, x_test, show=False)
        plt.savefig('shap_summary.png')
        
    def shap_collective(self):
        shap.initjs()
        x_test=self.test_x
        z=shap.sample(x_test,nsamples=100)
        explainer=shap.KernelExplainer(self.cat.predict,z)
        k_shap_values = explainer.shap_values(x_test)
        
        return shap.force_plot(explainer.expected_value, k_shap_values, x_test)
        #plt.clf()
        #plt.savefig('shap_collective.png')

    def performance(self):
        y_test=self.test_y
        y_test=np.array(y_test)
        predictions=self.pred
                
        # Confusion matrix
        print(confusion_matrix(y_test, predictions))

        # Accuracy, Precision, Recall, F1 score
        print(classification_report(y_test, predictions))


    def evaluate(self):
        """This function generates the evaluation report for the model"""
        pred = self.pred
        print('check pred')
        (self.fpr, self.tpr, self.thresholds) = roc_curve(y_true=self.test_y, y_score=pred)
        print('fpr, tpr, thresh check')
        self.fnr = 1- self.tpr
        print('fnr check')
        self.roc_auc = auc(self.fpr, self.tpr)
        print('roc_Auc check')
        self.precision, self.recall, _ = precision_recall_curve(self.test_y, pred)
        print('precision recall check')
        self.pr_auc = auc(self.recall, self.precision)
        print('pr_auc check')
        eval_list = ['roc', 'prcurve', 'fpr_fnr']
        for eval_name in eval_list:
            func = getattr(self,eval_name)
            func()
        else:
            print('Not valid evaluation type')   
    def roc(self):
        fpr, tpr, roc = self.fpr, self.tpr, self.roc_auc

        plt.figure(figsize=(16, 8))
        lw = 2
        plt.plot(fpr, tpr, color='darkorange',
                 lw=lw, label='ROC curve (area = %0.2f)' % self.roc_auc, alpha=0.5)
        plt.plot([0, 1], [0, 1], color='navy', lw=lw, linestyle='--', alpha=0.5)
        plt.xlim([0.0, 1.0])
        plt.ylim([0.0, 1.05])
        plt.xticks(fontsize=16)
        plt.yticks(fontsize=16)
        plt.grid(True)
        plt.xlabel('False Positive Rate', fontsize=16)
        plt.ylabel('True Positive Rate', fontsize=16)
        plt.title('Receiver operating characteristic', fontsize=20)
        plt.legend(loc="lower right", fontsize=16)
        plt.savefig('roc.png')
        
    def prcurve(self):
        test_y=self.test_y
        recall, precision, pr_auc = self.recall, self.precision, self.pr_auc
        # plot the precision-recall curves
        no_skill = len(test_y[test_y==1]) / len(test_y)
        plt.figure(figsize = (16,8))
        plt.plot([0, 1], [no_skill, no_skill], color='navy', linestyle='--',
                 alpha=0.5)
        plt.plot(recall, precision, color='darkorange',
                 label='ROC curve (area = %0.2f)'% pr_auc, alpha=0.5)
        # axis labels
        plt.title('Precision Recall Curve', size = 20)
        plt.xlabel('Recall', fontsize=16)
        plt.ylabel('Precision', fontsize=16)
        plt.grid(True)
        plt.xticks(fontsize=16)
        plt.yticks(fontsize=16)
        # show the legend
        plt.legend(fontsize=16)
        plt.savefig('prcurve.png')
    
    def fpr_fnr(self):
        lw = 2
        fpr, fnr, thresholds = self.fpr, self.fnr, self.thresholds
        plt.figure(figsize = (16,8))
        plt.plot(thresholds, fpr, color='blue', lw=lw, label='FPR', alpha=0.5)
        plt.plot(thresholds, fnr, color='green', lw=lw, label='FNR', alpha=0.5)
        plt.xlim([0.0, 1.0])
        plt.ylim([0.0, 1.05])
        plt.xticks(fontsize=16)
        plt.yticks(fontsize=16)
        plt.grid(True)
        plt.xlabel('Threshold', fontsize=16)
        plt.ylabel('Error Rate', fontsize=16)
        plt.title('FPR-FNR curves', fontsize=20)
        plt.legend(loc="lower left", fontsize=16)
        plt.savefig('fpr-fnr.png')
        




        




